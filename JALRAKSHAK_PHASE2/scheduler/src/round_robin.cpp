// Round Robin for water: requests wait in a queue; each turn a request receives up to
// `quantum_litres` and, if it still needs more, goes to the back of the queue. Nobody waits
// for a big request to finish, and everyone makes progress until the water runs out.
// Requests that arrive while a turn is running join the queue before the unfinished one rejoins.
// Note: because water already handed out cannot be taken back, min_allocation_ratio is not applied here.
#include <algorithm>
#include <deque>
#include <limits>
#include <numeric>
#include <stdexcept>

#include "sequential.hpp"

namespace jal {
namespace {

class RoundRobin : public Scheduler {
public:
    std::string name() const override { return "round_robin"; }

    std::vector<Allocation> run(const Input& in) const override {
        const size_t n = in.requests.size();
        std::vector<Allocation> plan;
        if (n == 0) return plan;

        double total_requested = 0;
        for (const Request& r : in.requests) total_requested += r.requested_litres;
        if (total_requested / in.config.quantum_litres > 5e6)
            throw std::runtime_error("config.quantum_litres is too small for this much water");

        std::vector<size_t> order(n);
        std::iota(order.begin(), order.end(), size_t{0});
        std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) {
            const Request& x = in.requests[a];
            const Request& y = in.requests[b];
            if (x.arrival_time != y.arrival_time) return x.arrival_time < y.arrival_time;
            return x.request_id < y.request_id;
        });

        const double inf = std::numeric_limits<double>::infinity();
        std::vector<double> got(n, 0.0), start(n, inf), finish(n, 0.0);
        std::deque<size_t> queue;
        size_t next = 0;
        double clock = initial_clock(in);
        double remaining = in.available_litres;

        auto admit = [&]() {
            while (next < n && in.requests[order[next]].arrival_time <= clock + kEps)
                queue.push_back(order[next++]);
        };
        admit();

        while (remaining > kEps) {
            if (queue.empty()) {
                if (next >= n) break;
                clock = std::max(clock, in.requests[order[next]].arrival_time);
                admit();
                continue;
            }
            const size_t i = queue.front();
            queue.pop_front();
            const Request& r = in.requests[i];
            const double grant = std::min({in.config.quantum_litres, r.requested_litres - got[i], remaining});
            if (start[i] == inf) start[i] = clock;
            got[i] += grant;
            remaining -= grant;
            clock += grant / in.config.flow_rate_lpm;
            finish[i] = clock;
            admit();
            if (r.requested_litres - got[i] > kEps) queue.push_back(i);
        }

        // Output order: by first service time, then never-served requests by arrival.
        std::vector<size_t> out(order);
        std::stable_sort(out.begin(), out.end(), [&](size_t a, size_t b) { return start[a] < start[b]; });
        for (size_t i : out) {
            const Request& r = in.requests[i];
            Allocation a;
            a.request_id = r.request_id;
            a.allocated_litres = std::min(got[i], r.requested_litres);
            if (got[i] > kEps) {
                a.start_time = start[i];
                a.finish_time = finish[i];
                a.wait_time = start[i] - r.arrival_time;
                a.status = got[i] >= r.requested_litres - kEps ? "approved" : "partial";
            } else {
                a.allocated_litres = 0;
                a.status = "rejected";
            }
            plan.push_back(a);
        }
        return plan;
    }
};

}  // namespace

std::unique_ptr<Scheduler> make_round_robin() { return std::make_unique<RoundRobin>(); }

}  // namespace jal

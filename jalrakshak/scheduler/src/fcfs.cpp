// First-Come-First-Served: serve requests strictly by arrival time (ties: request_id).
// Single pipeline model: a request of L litres occupies the pipeline for L / flow_rate minutes.
#include <algorithm>

#include "scheduler.hpp"

namespace jal {
namespace {

class Fcfs : public Scheduler {
public:
    std::string name() const override { return "fcfs"; }

    std::vector<Allocation> run(const Input& in) const override {
        std::vector<const Request*> order;
        for (const Request& r : in.requests) order.push_back(&r);
        std::stable_sort(order.begin(), order.end(), [](const Request* a, const Request* b) {
            if (a->arrival_time != b->arrival_time) return a->arrival_time < b->arrival_time;
            return a->request_id < b->request_id;
        });

        std::vector<Allocation> plan;
        double remaining = in.available_litres;
        double clock = 0;

        for (const Request* r : order) {
            Allocation a;
            a.request_id = r->request_id;

            double grant = std::min(r->requested_litres, remaining);
            bool too_small = grant < in.config.min_allocation_ratio * r->requested_litres - kEps;
            if (grant <= kEps || too_small) {
                a.allocated_litres = 0;
                a.status = "rejected";
                plan.push_back(a);
                continue;
            }

            double start = std::max(clock, r->arrival_time);
            double finish = start + grant / in.config.flow_rate_lpm;
            a.allocated_litres = grant;
            a.start_time = start;
            a.finish_time = finish;
            a.wait_time = start - r->arrival_time;
            a.status = (grant >= r->requested_litres - kEps) ? "approved" : "partial";
            plan.push_back(a);

            remaining -= grant;
            clock = finish;
        }
        return plan;
    }
};

}  // namespace

std::unique_ptr<Scheduler> make_fcfs() { return std::make_unique<Fcfs>(); }

}  // namespace jal

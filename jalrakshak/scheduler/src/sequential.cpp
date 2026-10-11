#include "sequential.hpp"

#include <algorithm>
#include <limits>
#include <stdexcept>

namespace jal {

double initial_clock(const Input& in) {
    if (in.requests.empty() || in.config.batch_window_minutes <= 0) return 0;
    double first = std::numeric_limits<double>::infinity();
    for (const Request& r : in.requests) first = std::min(first, r.arrival_time);
    return first + in.config.batch_window_minutes;
}

std::vector<Allocation> run_sequential(const Input& in, const Picker& pick) {
    const size_t n = in.requests.size();
    std::vector<Allocation> plan;
    plan.reserve(n);
    if (n == 0) return plan;

    std::vector<bool> done(n, false);
    SeqState st;
    st.clock = initial_clock(in);
    st.remaining = in.available_litres;

    for (size_t handled = 0; handled < n; ++handled) {
        st.pending_litres = 0;
        for (size_t i = 0; i < n; ++i)
            if (!done[i]) st.pending_litres += in.requests[i].requested_litres;

        auto collect_ready = [&]() {
            std::vector<size_t> ready;
            for (size_t i = 0; i < n; ++i)
                if (!done[i] && in.requests[i].arrival_time <= st.clock + kEps) ready.push_back(i);
            return ready;
        };

        std::vector<size_t> ready = collect_ready();
        if (ready.empty()) {  // pipeline idle: jump to the next arrival
            double next = std::numeric_limits<double>::infinity();
            for (size_t i = 0; i < n; ++i)
                if (!done[i]) next = std::min(next, in.requests[i].arrival_time);
            st.clock = next;
            ready = collect_ready();
        }

        Pick p = pick(ready, st);
        if (p.index >= n || done[p.index] ||
            std::find(ready.begin(), ready.end(), p.index) == ready.end())
            throw std::logic_error("scheduler picked a request that is not ready");
        done[p.index] = true;

        const Request& r = in.requests[p.index];
        Allocation a;
        a.request_id = r.request_id;
        a.score = p.score;

        double grant = std::min(r.requested_litres, st.remaining);
        if (p.max_grant) grant = std::min(grant, std::max(0.0, *p.max_grant));
        const bool too_small = grant < in.config.min_allocation_ratio * r.requested_litres - kEps;
        if (grant <= kEps || too_small) {
            a.allocated_litres = 0;
            a.status = "rejected";
        } else {
            const double start = st.clock;
            const double finish = start + grant / in.config.flow_rate_lpm;
            a.allocated_litres = grant;
            a.start_time = start;
            a.finish_time = finish;
            a.wait_time = start - r.arrival_time;
            a.status = (grant >= r.requested_litres - kEps) ? "approved" : "partial";
            st.remaining -= grant;
            st.clock = finish;
        }
        plan.push_back(a);
    }
    return plan;
}

}  // namespace jal

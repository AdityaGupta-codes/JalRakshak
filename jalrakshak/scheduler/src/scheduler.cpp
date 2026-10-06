#include "scheduler.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <stdexcept>

namespace jal {

std::unique_ptr<Scheduler> make_fcfs();  // src/fcfs.cpp

std::unique_ptr<Scheduler> make_scheduler(const std::string& algorithm) {
    if (algorithm == "fcfs") return make_fcfs();
    if (algorithm == "priority" || algorithm == "round_robin" || algorithm == "water_aware")
        throw std::runtime_error("algorithm '" + algorithm + "' is not implemented yet");
    throw std::runtime_error("unknown algorithm '" + algorithm +
                             "' (expected fcfs | priority | round_robin | water_aware)");
}

void check_invariants(const Input& in, const std::vector<Allocation>& allocs) {
    std::map<int, const Request*> by_id;
    for (const Request& r : in.requests) by_id[r.request_id] = &r;

    if (allocs.size() != in.requests.size())
        throw std::runtime_error("invariant violated: every request must appear exactly once");

    double total = 0;
    std::map<int, int> seen;
    for (const Allocation& a : allocs) {
        auto it = by_id.find(a.request_id);
        if (it == by_id.end()) throw std::runtime_error("invariant violated: unknown request_id in plan");
        if (++seen[a.request_id] > 1) throw std::runtime_error("invariant violated: request allocated twice");
        if (a.allocated_litres < -kEps) throw std::runtime_error("invariant violated: negative allocation");
        if (a.allocated_litres > it->second->requested_litres + 1e-6)
            throw std::runtime_error("invariant violated: allocation exceeds request");
        total += a.allocated_litres;
    }
    if (total > in.available_litres + 1e-6)
        throw std::runtime_error("invariant violated: total allocation exceeds available water");
}

Metrics compute_metrics(const Input& in, const std::vector<Allocation>& allocs) {
    std::map<int, const Request*> by_id;
    for (const Request& r : in.requests) by_id[r.request_id] = &r;

    Metrics m;
    double sum_alloc = 0, sum_req = 0, wait_sum = 0, prio_sum = 0, prio_sat = 0;
    double sum_x = 0, sum_x2 = 0;
    int served = 0;

    for (const Allocation& a : allocs) {
        const Request& r = *by_id.at(a.request_id);
        double x = a.allocated_litres / r.requested_litres;  // satisfaction of this request
        sum_alloc += a.allocated_litres;
        sum_req += r.requested_litres;
        prio_sum += r.priority;
        prio_sat += r.priority * x;
        sum_x += x;
        sum_x2 += x * x;
        if (a.allocated_litres > kEps) {
            ++served;
            if (a.wait_time) wait_sum += *a.wait_time;
        } else {
            ++m.unserved_count;
        }
    }

    m.avg_wait_time = served ? wait_sum / served : 0;
    m.utilization = in.available_litres > kEps ? sum_alloc / in.available_litres : 0;
    m.satisfaction_ratio = sum_req > kEps ? sum_alloc / sum_req : 0;
    m.priority_weighted_satisfaction = prio_sum > kEps ? prio_sat / prio_sum : 0;
    const double n = static_cast<double>(allocs.size());
    m.jain_fairness = (n > 0 && sum_x2 > kEps) ? (sum_x * sum_x) / (n * sum_x2) : 0;
    return m;
}

Output execute(const Input& in) {
    std::unique_ptr<Scheduler> s = make_scheduler(in.algorithm);
    Output out;
    out.algorithm = in.algorithm;
    out.allocations = s->run(in);
    check_invariants(in, out.allocations);

    double total = 0;
    for (const Allocation& a : out.allocations) total += a.allocated_litres;
    out.remaining_litres = std::max(0.0, in.available_litres - total);
    out.metrics = compute_metrics(in, out.allocations);
    return out;
}

}  // namespace jal

#include "scheduler.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <stdexcept>

namespace jal {

std::unique_ptr<Scheduler> make_fcfs();         // src/fcfs.cpp
std::unique_ptr<Scheduler> make_priority();     // src/priority.cpp
std::unique_ptr<Scheduler> make_round_robin();  // src/round_robin.cpp
std::unique_ptr<Scheduler> make_water_aware();  // src/water_aware.cpp
std::unique_ptr<Scheduler> make_manual();       // src/manual.cpp

std::unique_ptr<Scheduler> make_scheduler(const std::string& algorithm) {
    if (algorithm == "fcfs") return make_fcfs();
    if (algorithm == "priority") return make_priority();
    if (algorithm == "round_robin") return make_round_robin();
    if (algorithm == "water_aware") return make_water_aware();
    if (algorithm == "manual") return make_manual();
    throw std::runtime_error("unknown algorithm '" + algorithm +
                             "' (expected fcfs | priority | round_robin | water_aware | manual)");
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
            if (a.finish_time && *a.finish_time > r.deadline + 1e-6) ++m.deadline_miss_count;
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

// Litres are stored with two decimals (0.01 L) in the database. Rounding every grant DOWN to a whole
// 0.01 L here keeps the plan cent-exact and guarantees the rounded plan never uses more water than
// is available. A grant that covers the whole request is set to exactly the requested amount.
static void round_down_to_cents(const Input& in, std::vector<Allocation>& allocs) {
    std::map<int, const Request*> by_id;
    for (const Request& r : in.requests) by_id[r.request_id] = &r;
    for (Allocation& a : allocs) {
        auto it = by_id.find(a.request_id);
        if (it == by_id.end()) continue;  // check_invariants reports unknown ids
        const double requested = it->second->requested_litres;
        if (a.allocated_litres >= requested - kEps) a.allocated_litres = requested;
        else a.allocated_litres = std::floor(a.allocated_litres * 100.0 + 1e-9) / 100.0;

        if (a.allocated_litres <= kEps) {  // rounded away to nothing: this request got no water
            a.allocated_litres = 0;
            a.status = "rejected";
            a.start_time.reset();
            a.finish_time.reset();
            a.wait_time.reset();
        } else {
            a.status = (a.allocated_litres >= requested - kEps) ? "approved" : "partial";
        }
    }
}

Output execute(const Input& in) {
    std::unique_ptr<Scheduler> s = make_scheduler(in.algorithm);
    Output out;
    out.algorithm = in.algorithm;
    out.allocations = s->run(in);
    round_down_to_cents(in, out.allocations);
    check_invariants(in, out.allocations);

    double total = 0;
    for (const Allocation& a : out.allocations) total += a.allocated_litres;
    out.remaining_litres = std::max(0.0, in.available_litres - total);
    out.metrics = compute_metrics(in, out.allocations);
    return out;
}

}  // namespace jal

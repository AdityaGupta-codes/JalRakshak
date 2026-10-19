// Water-Aware scheduling (the project's own algorithm).
// Whenever the pipeline is free, every request that has arrived gets a score in [0, 1]:
//
//   score = ( w_priority * priority + w_urgency * urgency + w_scarcity * scarcity
//           + w_demand * demand_impact + w_fairness * fairness ) / (sum of weights)
//
//   priority      = priority / 5
//   urgency       = 0.5 * (urgency / 5) + 0.5 * time_pressure,
//                   time_pressure = 1 - clamp((deadline - now) / longest_deadline_window, 0, 1)
//   scarcity      = scarcity_level * (1 - share_of_remaining_water_this_request_takes)
//                   scarcity_level = clamp(expected_demand / water_left, 0, 2) / 2
//                   -> the scarcer the water, the more small requests are favoured, so more users are served.
//                      (A level that is identical for every request could never change the order.)
//   demand_impact = zone_population / largest_zone_population
//   fairness      = 1 - zone_received_ratio   (zones that were under-served before go first)
//
// The highest score is served next. Weights come from the config, never from code.
//
// Guarantee floor (config.guarantee_floor, default 0.3, or a per-request min_share set by the water
// policy for its class): when water is scarce, serving the best-scored request in full would leave the
// last ones with nothing. So each request is capped so that every request still waiting keeps its
// guaranteed share in reserve:
//   cap = max(own_share * requested, water_left - sum over the others of (their share * requested))
// If there is enough water for every floor, the best-scored requests get the extra water; if not, floors
// are served in score order. floor = 0 gives pure score order (most unequal, favours priority);
// a high floor gives nearly equal shares (fairest, ignores priority).
#include <algorithm>
#include <cmath>
#include <stdexcept>

#include "sequential.hpp"

namespace jal {
namespace {

double clamp01(double v) { return std::max(0.0, std::min(1.0, v)); }

class WaterAware : public Scheduler {
public:
    std::string name() const override { return "water_aware"; }

    std::vector<Allocation> run(const Input& in) const override {
        const Weights& w = in.config.weights;
        if (w.priority < 0 || w.urgency < 0 || w.scarcity < 0 || w.demand_impact < 0 || w.fairness < 0)
            throw std::runtime_error("config.weights must not be negative");
        const double wsum = w.priority + w.urgency + w.scarcity + w.demand_impact + w.fairness;
        // wsum == 0 (all weights zero) is allowed: every score is 0 and ties are broken by arrival order.

        double horizon = 1, max_pop = 0;
        for (const Request& r : in.requests) {
            horizon = std::max(horizon, r.deadline - r.arrival_time);
            max_pop = std::max(max_pop, static_cast<double>(r.zone_population));
        }

        auto score_of = [&](const Request& r, const SeqState& st) {
            const double left = std::max(st.remaining, kEps);
            const double demand = in.predicted_demand_litres ? *in.predicted_demand_litres : st.pending_litres;
            const double level = std::min(std::max(demand / left, 0.0), 2.0) / 2.0;
            const double share = clamp01(r.requested_litres / left);

            const double f_priority = r.priority / 5.0;
            const double pressure = 1.0 - clamp01((r.deadline - st.clock) / horizon);
            const double f_urgency = 0.5 * (r.urgency / 5.0) + 0.5 * pressure;
            const double f_scarcity = level * (1.0 - share);
            const double f_demand = max_pop > 0 ? r.zone_population / max_pop : 0.0;
            const double f_fair = 1.0 - clamp01(r.zone_received_ratio);

            return (w.priority * f_priority + w.urgency * f_urgency + w.scarcity * f_scarcity +
                    w.demand_impact * f_demand + w.fairness * f_fair) / (wsum > 0 ? wsum : 1.0);
        };

        return run_sequential(in, [&](const std::vector<size_t>& ready, const SeqState& st) {
            size_t best = ready.front();
            double best_score = -1;
            for (size_t i : ready) {
                const double s = score_of(in.requests[i], st);
                const Request& a = in.requests[i];
                const Request& b = in.requests[best];
                const bool better = s > best_score + 1e-12 ||
                    (std::fabs(s - best_score) <= 1e-12 &&
                     (a.arrival_time != b.arrival_time ? a.arrival_time < b.arrival_time
                                                       : a.request_id < b.request_id));
                if (better) { best = i; best_score = s; }
            }
            const Request& chosen = in.requests[best];
            // Every request still waiting keeps its guaranteed share in reserve. The share comes from the
            // request's policy class (min_share, e.g. hospitals 0.5) or else from config.guarantee_floor.
            const double own_floor = floor_share_of(in, chosen) * chosen.requested_litres;
            const double others_floor = std::max(0.0, st.pending_floor_litres - own_floor);
            const double cap = std::max(own_floor, st.remaining - others_floor);
            return Pick{best, best_score, cap};
        });
    }
};

}  // namespace

std::unique_ptr<Scheduler> make_water_aware() { return std::make_unique<WaterAware>(); }

}  // namespace jal

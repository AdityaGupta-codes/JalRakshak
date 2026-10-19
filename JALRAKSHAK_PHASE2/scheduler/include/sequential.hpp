// Shared simulation engine for schedulers that serve one request at a time on a single pipeline
// (FCFS, Priority, Water-Aware). The scheduler only supplies a "picker": which of the requests that
// have already arrived should be served next. The engine handles the clock, water accounting,
// minimum-allocation rule and timing, so all algorithms are compared under identical rules.
#pragma once
#include <functional>
#include <optional>
#include <vector>

#include "scheduler.hpp"

namespace jal {

struct SeqState {
    double clock = 0;           // current simulation minute
    double remaining = 0;       // litres still in the reservoir
    double pending_litres = 0;  // litres still requested by requests not yet handled (arrived or not)
    double pending_floor_litres = 0;  // sum of (guaranteed share x requested) over requests not yet handled
};

// Guaranteed share of a request under the water_aware floor: its policy class share, or the config floor.
double floor_share_of(const Input& in, const Request& r);

struct Pick {
    size_t index = 0;               // index into Input::requests
    std::optional<double> score;    // reported in the output (water_aware only)
    std::optional<double> max_grant;  // upper limit on litres for this turn (water_aware guarantee floor)
};

// `ready` holds indices of requests that have arrived by st.clock and are not handled yet.
using Picker = std::function<Pick(const std::vector<size_t>& ready, const SeqState& st)>;

// Minute at which the scheduler starts working (0, or first arrival + batch window).
double initial_clock(const Input& in);

std::vector<Allocation> run_sequential(const Input& in, const Picker& pick);

}  // namespace jal

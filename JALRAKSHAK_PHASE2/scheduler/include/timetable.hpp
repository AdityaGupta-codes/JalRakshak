// Zone-wise daily supply timetable ("mode": "timetable").
//
// The request schedulers decide how one reservoir's stored water is shared among organisations. A water
// department also runs a daily piped-supply timetable: each zone is fed from a source reservoir for some
// hours, limited by how much the source can release today, how fast its pumps can deliver, and how much the
// zone's inlet main can carry. This module produces that timetable.
//
//   1. Demand per zone  = demand_litres (e.g. the ML forecast) or population x lpcd (litres per person per day).
//   2. Target per zone  = min(demand, inlet capacity x hours in the supply window).
//   3. Sharing per source (when the source cannot meet every target): every zone first receives its
//      guaranteed share (min_share x target), then the rest is shared in proportion to priority x remaining need
//      (weighted water-filling), never above a zone's target.
//   4. Timing: each source's pumps deliver at most pump_capacity_lph per hour. Zones are placed, most
//      important first, in the earliest continuous block of slots with enough pump capacity; whatever does
//      not fit in one block is placed in the remaining free capacity (a second block).
//   5. Report: litres per zone, litres per person (lpcd) against the norm, hours of supply, shortfall.
#pragma once
#include <string>
#include <vector>

#include "json.hpp"

namespace jal {

struct TtSource {
    int reservoir_id = 0;
    std::string name;
    double release_litres = 0;     // what this source can supply today
    double pump_capacity_lph = 0;  // litres per hour all its pumps together can deliver
};

struct TtZone {
    int zone_id = 0;
    std::string name;
    int population = 0;
    int reservoir_id = 0;           // source feeding this zone
    double inlet_capacity_lph = 0;  // hydraulic limit of the zone's inlet main
    double demand_litres = -1;      // -1 = population x lpcd
    int priority = 3;               // 1..5 (policy)
    double min_share = 0.3;         // guaranteed share of target when water is short
};

struct TtInput {
    double lpcd = 135;  // norm, litres per person per day
    int day_start_hour = 5;
    int day_end_hour = 23;
    int slot_minutes = 60;
    std::vector<TtSource> sources;
    std::vector<TtZone> zones;
};

struct TtBlock {
    int start_minute = 0;  // minutes after midnight
    int end_minute = 0;
    double litres = 0;
};

struct TtZoneResult {
    int zone_id = 0;
    double demand = 0, target = 0, allocated = 0, delivered = 0;
    double hours = 0;  // total supply hours
    double lpcd_delivered = 0;
    std::vector<TtBlock> blocks;
    std::vector<double> slot_litres;  // litres in each slot of the window (for checks)
};

struct TtSourceResult {
    int reservoir_id = 0;
    double release = 0, delivered = 0;
    double pump_utilization = 0;  // delivered / (pump capacity x window hours)
};

struct TtOutput {
    std::vector<TtZoneResult> zones;
    std::vector<TtSourceResult> sources;
    double total_demand = 0, total_delivered = 0;
    double min_lpcd = 0;
    int zones_below_norm = 0;
    double jain_fairness = 0;  // over delivered / demand per zone
};

TtInput parse_timetable(const json::Value& root);
TtOutput build_timetable(const TtInput& in);
json::Value timetable_to_json(const TtInput& in, const TtOutput& out);

}  // namespace jal

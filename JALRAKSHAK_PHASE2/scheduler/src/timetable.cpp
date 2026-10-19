#include "timetable.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <map>
#include <set>
#include <stdexcept>

namespace jal {
namespace {

double num_field(const json::Value& o, const std::string& key, const std::string& where) {
    const json::Value* v = o.find(key);
    if (!v || !v->is_number()) throw std::runtime_error(where + ": '" + key + "' must be a number");
    return v->as_number();
}

double opt_field(const json::Value& o, const std::string& key, double fallback, const std::string& where) {
    const json::Value* v = o.find(key);
    if (!v || v->is_null()) return fallback;
    if (!v->is_number()) throw std::runtime_error(where + ": '" + key + "' must be a number");
    return v->as_number();
}

int int_field(double d, const std::string& what) {
    if (std::floor(d) != d) throw std::runtime_error(what + " must be an integer");
    return static_cast<int>(d);
}

std::string str_field(const json::Value& o, const std::string& key) {
    const json::Value* v = o.find(key);
    return (v && v->is_string()) ? v->as_string() : std::string();
}

std::string clock_text(int minute) {
    char buf[16];
    std::snprintf(buf, sizeof buf, "%02d:%02d", minute / 60, minute % 60);
    return buf;
}

json::Value n(double d) { return json::Value::number(std::round(d * 100.0) / 100.0); }

// Weighted water-filling: give `budget` litres to zones with room (target - already given), in proportion
// to their weight, never above the room. Returns the litres actually given.
double water_fill(std::vector<double>& given, const std::vector<double>& target,
                  const std::vector<double>& weight, double budget) {
    double spent = 0;
    std::vector<bool> active(given.size());
    for (size_t i = 0; i < given.size(); ++i) active[i] = target[i] - given[i] > 1e-9 && weight[i] > 0;
    while (budget - spent > 1e-6) {
        double wsum = 0;
        for (size_t i = 0; i < given.size(); ++i) if (active[i]) wsum += weight[i];
        if (wsum <= 0) break;
        const double pool = budget - spent;
        bool capped = false;
        for (size_t i = 0; i < given.size(); ++i) {
            if (!active[i]) continue;
            const double room = target[i] - given[i];
            if (pool * weight[i] / wsum >= room - 1e-9) {  // this zone fills up: give its room, recompute
                given[i] += room;
                spent += room;
                active[i] = false;
                capped = true;
            }
        }
        if (capped) continue;
        for (size_t i = 0; i < given.size(); ++i)
            if (active[i]) {
                const double share = pool * weight[i] / wsum;
                given[i] += share;
                spent += share;
            }
        break;
    }
    return spent;
}

}  // namespace

TtInput parse_timetable(const json::Value& root) {
    if (!root.is_object()) throw std::runtime_error("input must be a JSON object");
    TtInput in;
    in.lpcd = opt_field(root, "lpcd", in.lpcd, "input");
    in.day_start_hour = int_field(opt_field(root, "day_start_hour", in.day_start_hour, "input"), "day_start_hour");
    in.day_end_hour = int_field(opt_field(root, "day_end_hour", in.day_end_hour, "input"), "day_end_hour");
    in.slot_minutes = int_field(opt_field(root, "slot_minutes", in.slot_minutes, "input"), "slot_minutes");

    const json::Value& sources = root.at("sources");
    if (!sources.is_array()) throw std::runtime_error("'sources' must be an array");
    size_t i = 0;
    for (const json::Value& s : sources.items()) {
        const std::string w = "sources[" + std::to_string(i++) + "]";
        if (!s.is_object()) throw std::runtime_error(w + " must be an object");
        TtSource x;
        x.reservoir_id = int_field(num_field(s, "reservoir_id", w), w + ".reservoir_id");
        x.name = str_field(s, "name");
        x.release_litres = num_field(s, "release_litres", w);
        x.pump_capacity_lph = num_field(s, "pump_capacity_lph", w);
        in.sources.push_back(x);
    }
    const json::Value& zones = root.at("zones");
    if (!zones.is_array()) throw std::runtime_error("'zones' must be an array");
    i = 0;
    for (const json::Value& z : zones.items()) {
        const std::string w = "zones[" + std::to_string(i++) + "]";
        if (!z.is_object()) throw std::runtime_error(w + " must be an object");
        TtZone x;
        x.zone_id = int_field(num_field(z, "zone_id", w), w + ".zone_id");
        x.name = str_field(z, "name");
        x.population = int_field(num_field(z, "population", w), w + ".population");
        x.reservoir_id = int_field(num_field(z, "reservoir_id", w), w + ".reservoir_id");
        x.inlet_capacity_lph = num_field(z, "inlet_capacity_lph", w);
        x.demand_litres = opt_field(z, "demand_litres", -1, w);
        x.priority = int_field(opt_field(z, "priority", 3, w), w + ".priority");
        x.min_share = opt_field(z, "min_share", 0.3, w);
        in.zones.push_back(x);
    }

    // validation
    if (!(in.lpcd > 0)) throw std::runtime_error("lpcd must be > 0");
    if (in.day_start_hour < 0 || in.day_end_hour > 24 || in.day_start_hour >= in.day_end_hour)
        throw std::runtime_error("the supply window must satisfy 0 <= day_start_hour < day_end_hour <= 24");
    if (in.slot_minutes != 15 && in.slot_minutes != 30 && in.slot_minutes != 60)
        throw std::runtime_error("slot_minutes must be 15, 30 or 60");
    std::set<int> src_ids, zone_ids;
    for (const TtSource& s : in.sources) {
        if (!src_ids.insert(s.reservoir_id).second) throw std::runtime_error("duplicate source " + std::to_string(s.reservoir_id));
        if (!(s.release_litres >= 0)) throw std::runtime_error("source " + std::to_string(s.reservoir_id) + ": release_litres must be >= 0");
        if (!(s.pump_capacity_lph > 0)) throw std::runtime_error("source " + std::to_string(s.reservoir_id) + ": pump_capacity_lph must be > 0");
    }
    for (const TtZone& z : in.zones) {
        const std::string w = "zone " + std::to_string(z.zone_id);
        if (!zone_ids.insert(z.zone_id).second) throw std::runtime_error("duplicate zone " + std::to_string(z.zone_id));
        if (z.population <= 0) throw std::runtime_error(w + ": population must be > 0");
        if (!src_ids.count(z.reservoir_id)) throw std::runtime_error(w + ": its source reservoir is not in 'sources'");
        if (!(z.inlet_capacity_lph > 0)) throw std::runtime_error(w + ": inlet_capacity_lph must be > 0");
        if (z.demand_litres != -1 && !(z.demand_litres >= 0)) throw std::runtime_error(w + ": demand_litres must be >= 0");
        if (z.priority < 1 || z.priority > 5) throw std::runtime_error(w + ": priority must be 1..5");
        if (!(z.min_share >= 0 && z.min_share <= 1)) throw std::runtime_error(w + ": min_share must be within [0, 1]");
    }
    return in;
}

TtOutput build_timetable(const TtInput& in) {
    const double hours = in.day_end_hour - in.day_start_hour;
    const int nslots = static_cast<int>(hours * 60) / in.slot_minutes;
    const double slot_h = in.slot_minutes / 60.0;

    TtOutput out;
    std::map<int, size_t> zone_index;
    out.zones.resize(in.zones.size());
    for (size_t i = 0; i < in.zones.size(); ++i) {
        const TtZone& z = in.zones[i];
        TtZoneResult& r = out.zones[i];
        r.zone_id = z.zone_id;
        r.demand = z.demand_litres >= 0 ? z.demand_litres : z.population * in.lpcd;
        r.target = std::min(r.demand, z.inlet_capacity_lph * hours);
        zone_index[z.zone_id] = i;
    }

    for (const TtSource& s : in.sources) {
        std::vector<size_t> members;
        for (size_t i = 0; i < in.zones.size(); ++i)
            if (in.zones[i].reservoir_id == s.reservoir_id) members.push_back(i);

        // ---- 1. how much each zone gets today
        const double budget = std::min(s.release_litres, s.pump_capacity_lph * hours);
        std::vector<double> target, given, weight;
        double total_target = 0, total_floor = 0;
        for (size_t i : members) {
            target.push_back(out.zones[i].target);
            total_target += out.zones[i].target;
            total_floor += in.zones[i].min_share * out.zones[i].target;
            // share of the extra water ~ priority x what the zone still needs above its floor
            weight.push_back(in.zones[i].priority * (1.0 - in.zones[i].min_share) * out.zones[i].target);
        }
        given.assign(members.size(), 0.0);
        if (total_target <= budget + 1e-9) {
            given = target;
        } else if (total_floor >= budget) {  // not even the guaranteed shares fit: scale them down equally
            for (size_t k = 0; k < members.size(); ++k)
                given[k] = total_floor > 0 ? in.zones[members[k]].min_share * target[k] * budget / total_floor : 0;
        } else {
            for (size_t k = 0; k < members.size(); ++k) given[k] = in.zones[members[k]].min_share * target[k];
            water_fill(given, target, weight, budget - total_floor);
        }
        for (size_t k = 0; k < members.size(); ++k)
            out.zones[members[k]].allocated = std::floor(given[k] + 1e-6);  // whole litres, never more

        // ---- 2. when: place zones in pump-capacity slots, most important first
        const double slot_cap = std::floor(s.pump_capacity_lph * slot_h);
        std::vector<double> free(nslots, slot_cap);
        std::vector<size_t> order = members;
        std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) {
            if (in.zones[a].priority != in.zones[b].priority) return in.zones[a].priority > in.zones[b].priority;
            return in.zones[a].zone_id < in.zones[b].zone_id;
        });
        double source_delivered = 0;
        for (size_t i : order) {
            TtZoneResult& r = out.zones[i];
            const double rate = std::floor(in.zones[i].inlet_capacity_lph * slot_h);  // litres per slot
            std::vector<double> put(nslots, 0.0);
            double need = r.allocated;
            if (need > 0 && rate > 0) {
                const int k = static_cast<int>(std::ceil(need / rate - 1e-9));
                int start = -1;
                for (int j = 0; j + k <= nslots && start < 0; ++j) {
                    bool fits = true;
                    double left = need;
                    for (int t = j; t < j + k; ++t) {
                        const double amount = std::min(rate, left);
                        if (free[t] + 1e-9 < amount) { fits = false; break; }
                        left -= amount;
                    }
                    if (fits) start = j;
                }
                if (start >= 0) {  // one continuous block
                    for (int t = start; t < start + k && need > 1e-9; ++t) {
                        const double amount = std::min(rate, need);
                        put[t] = amount;
                        need -= amount;
                    }
                } else {  // split: use whatever capacity is left, earliest first
                    for (int t = 0; t < nslots && need > 1e-9; ++t) {
                        const double amount = std::min({rate, need, free[t]});
                        if (amount > 1e-9) { put[t] = amount; need -= amount; }
                    }
                }
            }
            int used_slots = 0;
            r.slot_litres = put;
            for (int t = 0; t < nslots; ++t) {
                if (put[t] <= 1e-9) continue;
                free[t] -= put[t];
                r.delivered += put[t];
                ++used_slots;
                const int smin = in.day_start_hour * 60 + t * in.slot_minutes;
                if (!r.blocks.empty() && r.blocks.back().end_minute == smin) {
                    r.blocks.back().end_minute = smin + in.slot_minutes;
                    r.blocks.back().litres += put[t];
                } else {
                    r.blocks.push_back(TtBlock{smin, smin + in.slot_minutes, put[t]});
                }
            }
            r.hours = used_slots * slot_h;
            source_delivered += r.delivered;
        }
        out.sources.push_back(TtSourceResult{s.reservoir_id, s.release_litres, source_delivered,
                                             source_delivered / (s.pump_capacity_lph * hours)});

        // ---- invariants for this source
        if (source_delivered > s.release_litres + 1e-6)
            throw std::runtime_error("invariant violated: source " + std::to_string(s.reservoir_id) + " releases more than available");
        for (double f : free)
            if (f < -1e-6) throw std::runtime_error("invariant violated: pump capacity exceeded");
    }

    double sum_x = 0, sum_x2 = 0;
    out.min_lpcd = -1;
    for (size_t i = 0; i < out.zones.size(); ++i) {
        TtZoneResult& r = out.zones[i];
        if (r.delivered > r.demand + 1e-6) throw std::runtime_error("invariant violated: zone gets more than its demand");
        r.lpcd_delivered = r.delivered / in.zones[i].population;
        out.total_demand += r.demand;
        out.total_delivered += r.delivered;
        if (r.lpcd_delivered + 1e-9 < in.lpcd) ++out.zones_below_norm;
        out.min_lpcd = out.min_lpcd < 0 ? r.lpcd_delivered : std::min(out.min_lpcd, r.lpcd_delivered);
        const double x = r.demand > 0 ? r.delivered / r.demand : 1.0;
        sum_x += x;
        sum_x2 += x * x;
    }
    if (out.min_lpcd < 0) out.min_lpcd = 0;
    out.jain_fairness = (!out.zones.empty() && sum_x2 > 0) ? sum_x * sum_x / (out.zones.size() * sum_x2) : 0;
    return out;
}

json::Value timetable_to_json(const TtInput& in, const TtOutput& out) {
    json::Value root = json::Value::object();
    root.set("mode", json::Value::string("timetable"));
    root.set("lpcd_norm", n(in.lpcd));
    root.set("day_start_hour", json::Value::number(in.day_start_hour));
    root.set("day_end_hour", json::Value::number(in.day_end_hour));
    root.set("slot_minutes", json::Value::number(in.slot_minutes));
    json::Value zones = json::Value::array();
    for (size_t i = 0; i < out.zones.size(); ++i) {
        const TtZoneResult& r = out.zones[i];
        json::Value z = json::Value::object();
        z.set("zone_id", json::Value::number(r.zone_id));
        z.set("name", json::Value::string(in.zones[i].name));
        z.set("reservoir_id", json::Value::number(in.zones[i].reservoir_id));
        z.set("population", json::Value::number(in.zones[i].population));
        z.set("demand_litres", n(r.demand));
        z.set("target_litres", n(r.target));
        z.set("allocated_litres", n(r.allocated));
        z.set("delivered_litres", n(r.delivered));
        z.set("shortfall_litres", n(std::max(0.0, r.demand - r.delivered)));
        z.set("served_ratio", n(r.demand > 0 ? r.delivered / r.demand : 1.0));
        z.set("lpcd_delivered", n(r.lpcd_delivered));
        z.set("meets_norm", json::Value::boolean(r.lpcd_delivered + 1e-9 >= in.lpcd));
        z.set("supply_hours", n(r.hours));
        json::Value blocks = json::Value::array();
        for (const TtBlock& b : r.blocks) {
            json::Value o = json::Value::object();
            o.set("start_minute", json::Value::number(b.start_minute));
            o.set("end_minute", json::Value::number(b.end_minute));
            o.set("start", json::Value::string(clock_text(b.start_minute)));
            o.set("end", json::Value::string(clock_text(b.end_minute)));
            o.set("litres", n(b.litres));
            blocks.push(std::move(o));
        }
        z.set("blocks", std::move(blocks));
        zones.push(std::move(z));
    }
    root.set("zones", std::move(zones));
    json::Value sources = json::Value::array();
    for (const TtSourceResult& s : out.sources) {
        json::Value o = json::Value::object();
        o.set("reservoir_id", json::Value::number(s.reservoir_id));
        o.set("release_litres", n(s.release));
        o.set("delivered_litres", n(s.delivered));
        o.set("pump_utilization", n(s.pump_utilization));
        sources.push(std::move(o));
    }
    root.set("sources", std::move(sources));
    json::Value m = json::Value::object();
    m.set("total_demand_litres", n(out.total_demand));
    m.set("total_delivered_litres", n(out.total_delivered));
    m.set("served_ratio", n(out.total_demand > 0 ? out.total_delivered / out.total_demand : 1.0));
    m.set("min_lpcd", n(out.min_lpcd));
    m.set("zones_below_norm", json::Value::number(out.zones_below_norm));
    m.set("jain_fairness", n(out.jain_fairness));
    root.set("metrics", std::move(m));
    return root;
}

}  // namespace jal

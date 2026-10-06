#include <cmath>
#include <set>
#include <stdexcept>

#include "model.hpp"

namespace jal {
namespace {

double get_num(const json::Value& o, const std::string& key, const std::string& where) {
    const json::Value* v = o.find(key);
    if (!v || !v->is_number()) throw std::runtime_error(where + ": '" + key + "' must be a number");
    return v->as_number();
}

double opt_num(const json::Value& o, const std::string& key, double fallback, const std::string& where) {
    const json::Value* v = o.find(key);
    if (!v || v->is_null()) return fallback;
    if (!v->is_number()) throw std::runtime_error(where + ": '" + key + "' must be a number");
    return v->as_number();
}

int to_int(double d, const std::string& what) {
    if (std::floor(d) != d) throw std::runtime_error(what + " must be an integer");
    return static_cast<int>(d);
}

std::string opt_str(const json::Value& o, const std::string& key) {
    const json::Value* v = o.find(key);
    return (v && v->is_string()) ? v->as_string() : std::string();
}

Request parse_request(const json::Value& r, size_t idx) {
    const std::string where = "requests[" + std::to_string(idx) + "]";
    if (!r.is_object()) throw std::runtime_error(where + " must be an object");
    Request q;
    q.request_id = to_int(get_num(r, "request_id", where), where + ".request_id");
    q.requested_litres = get_num(r, "requested_litres", where);
    q.arrival_time = get_num(r, "arrival_time", where);
    q.user_id = to_int(opt_num(r, "user_id", 0, where), where + ".user_id");
    q.zone_id = to_int(opt_num(r, "zone_id", 0, where), where + ".zone_id");
    q.user_type = opt_str(r, "user_type");
    q.priority = to_int(opt_num(r, "priority", 3, where), where + ".priority");
    q.urgency = to_int(opt_num(r, "urgency", 3, where), where + ".urgency");
    q.deadline = opt_num(r, "deadline", q.arrival_time + 1440, where);
    q.zone_population = to_int(opt_num(r, "zone_population", 0, where), where + ".zone_population");
    q.zone_received_ratio = opt_num(r, "zone_received_ratio", 0, where);
    return q;
}

json::Value num(double d) {
    return json::Value::number(std::round(d * 10000.0) / 10000.0);
}

json::Value opt(const std::optional<double>& d) {
    return d ? num(*d) : json::Value();
}

}  // namespace

Input parse_input(const json::Value& root) {
    if (!root.is_object()) throw std::runtime_error("input must be a JSON object");
    Input in;
    const json::Value& alg = root.at("algorithm");
    if (!alg.is_string()) throw std::runtime_error("'algorithm' must be a string");
    in.algorithm = alg.as_string();
    in.available_litres = get_num(root, "available_litres", "input");

    if (const json::Value* p = root.find("predicted_demand_litres"))
        if (p->is_number()) in.predicted_demand_litres = p->as_number();

    if (const json::Value* c = root.find("config")) {
        if (!c->is_object()) throw std::runtime_error("'config' must be an object");
        in.config.flow_rate_lpm = opt_num(*c, "flow_rate_lpm", in.config.flow_rate_lpm, "config");
        in.config.quantum_litres = opt_num(*c, "quantum_litres", in.config.quantum_litres, "config");
        in.config.min_allocation_ratio =
            opt_num(*c, "min_allocation_ratio", in.config.min_allocation_ratio, "config");
        if (const json::Value* w = c->find("weights")) {
            if (!w->is_object()) throw std::runtime_error("'config.weights' must be an object");
            Weights& x = in.config.weights;
            x.priority = opt_num(*w, "priority", x.priority, "weights");
            x.urgency = opt_num(*w, "urgency", x.urgency, "weights");
            x.scarcity = opt_num(*w, "scarcity", x.scarcity, "weights");
            x.demand_impact = opt_num(*w, "demand_impact", x.demand_impact, "weights");
            x.fairness = opt_num(*w, "fairness", x.fairness, "weights");
        }
    }

    const json::Value& reqs = root.at("requests");
    if (!reqs.is_array()) throw std::runtime_error("'requests' must be an array");
    size_t i = 0;
    for (const json::Value& r : reqs.items()) in.requests.push_back(parse_request(r, i++));

    validate_input(in);
    return in;
}

void validate_input(const Input& in) {
    if (!(in.available_litres >= 0)) throw std::runtime_error("available_litres must be >= 0");
    if (!(in.config.flow_rate_lpm > 0)) throw std::runtime_error("config.flow_rate_lpm must be > 0");
    if (!(in.config.quantum_litres > 0)) throw std::runtime_error("config.quantum_litres must be > 0");
    if (!(in.config.min_allocation_ratio >= 0 && in.config.min_allocation_ratio <= 1))
        throw std::runtime_error("config.min_allocation_ratio must be within [0, 1]");

    std::set<int> seen;
    for (const Request& r : in.requests) {
        const std::string w = "request " + std::to_string(r.request_id);
        if (!seen.insert(r.request_id).second) throw std::runtime_error("duplicate request_id " + std::to_string(r.request_id));
        if (!(r.requested_litres > 0)) throw std::runtime_error(w + ": requested_litres must be > 0");
        if (r.arrival_time < 0) throw std::runtime_error(w + ": arrival_time must be >= 0");
        if (r.priority < 1 || r.priority > 5) throw std::runtime_error(w + ": priority must be 1..5");
        if (r.urgency < 1 || r.urgency > 5) throw std::runtime_error(w + ": urgency must be 1..5");
        if (r.zone_received_ratio < 0 || r.zone_received_ratio > 1)
            throw std::runtime_error(w + ": zone_received_ratio must be within [0, 1]");
    }
}

json::Value to_json(const Output& out) {
    json::Value root = json::Value::object();
    root.set("algorithm", json::Value::string(out.algorithm));

    json::Value allocs = json::Value::array();
    for (const Allocation& a : out.allocations) {
        json::Value o = json::Value::object();
        o.set("request_id", json::Value::number(a.request_id));
        o.set("allocated_litres", num(a.allocated_litres));
        o.set("start_time", opt(a.start_time));
        o.set("finish_time", opt(a.finish_time));
        o.set("wait_time", opt(a.wait_time));
        o.set("score", opt(a.score));
        o.set("status", json::Value::string(a.status));
        allocs.push(std::move(o));
    }
    root.set("allocations", std::move(allocs));
    root.set("remaining_litres", num(out.remaining_litres));

    json::Value m = json::Value::object();
    m.set("avg_wait_time", num(out.metrics.avg_wait_time));
    m.set("utilization", num(out.metrics.utilization));
    m.set("satisfaction_ratio", num(out.metrics.satisfaction_ratio));
    m.set("priority_weighted_satisfaction", num(out.metrics.priority_weighted_satisfaction));
    m.set("jain_fairness", num(out.metrics.jain_fairness));
    m.set("unserved_count", json::Value::number(out.metrics.unserved_count));
    root.set("metrics", std::move(m));
    return root;
}

}  // namespace jal

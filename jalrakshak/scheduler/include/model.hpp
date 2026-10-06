// Data model shared by all schedulers + JSON (de)serialisation.
// Field names follow docs/contracts.md.
#pragma once
#include <optional>
#include <string>
#include <vector>

#include "json.hpp"

namespace jal {

struct Request {
    int request_id = 0;
    int user_id = 0;
    int zone_id = 0;
    std::string user_type;
    int priority = 3;              // 1..5, 5 = highest
    int urgency = 3;               // 1..5
    double requested_litres = 0;
    double arrival_time = 0;       // simulation minutes
    double deadline = 0;           // simulation minutes
    int zone_population = 0;
    double zone_received_ratio = 0;  // 0..1, used by water_aware fairness
};

struct Weights {
    double priority = 0.30, urgency = 0.25, scarcity = 0.15, demand_impact = 0.15, fairness = 0.15;
};

struct Config {
    double flow_rate_lpm = 500;
    double quantum_litres = 2000;
    double min_allocation_ratio = 0.2;
    Weights weights;
};

struct Input {
    std::string algorithm;
    double available_litres = 0;
    std::optional<double> predicted_demand_litres;
    Config config;
    std::vector<Request> requests;
};

struct Allocation {
    int request_id = 0;
    double allocated_litres = 0;
    std::optional<double> start_time, finish_time, wait_time, score;
    std::string status;  // approved | partial | rejected
};

struct Metrics {
    double avg_wait_time = 0;
    double utilization = 0;
    double satisfaction_ratio = 0;
    double priority_weighted_satisfaction = 0;
    double jain_fairness = 0;
    int unserved_count = 0;
};

struct Output {
    std::string algorithm;
    std::vector<Allocation> allocations;
    double remaining_litres = 0;
    Metrics metrics;
};

// Throws std::runtime_error with a readable message on invalid input.
Input parse_input(const json::Value& root);
void validate_input(const Input& in);
json::Value to_json(const Output& out);

}  // namespace jal

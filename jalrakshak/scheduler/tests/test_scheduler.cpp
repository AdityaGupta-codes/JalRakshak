// Tiny dependency-free test runner (swap for GoogleTest later if desired).
#include <cmath>
#include <functional>
#include <iostream>
#include <vector>

#include "scheduler.hpp"

static int g_fail = 0, g_checks = 0;
#define CHECK(cond) do { ++g_checks; if (!(cond)) { ++g_fail; \
    std::cerr << "  FAIL " << __FILE__ << ":" << __LINE__ << "  " #cond "\n"; } } while (0)
#define NEAR(a, b) CHECK(std::fabs((a) - (b)) < 1e-4)

using namespace jal;

// The seed-data scarcity scenario: 50,000 L available, 80,000 L requested.
static Input seed_scenario() {
    Input in;
    in.algorithm = "fcfs";
    in.available_litres = 50000;
    struct Row { int id, prio; double litres, arrival; };
    const Row rows[] = {{1, 2, 20000, 0}, {2, 4, 12000, 5}, {3, 5, 10000, 10}, {4, 2, 8000, 12},
                        {5, 4, 6000, 15}, {6, 1, 4000, 20},  {7, 4, 15000, 25}, {8, 2, 5000, 30}};
    for (const Row& r : rows) {
        Request q;
        q.request_id = r.id; q.priority = r.prio; q.requested_litres = r.litres;
        q.arrival_time = r.arrival; q.deadline = r.arrival + 240;
        in.requests.push_back(q);
    }
    return in;
}

static Allocation find(const Output& o, int id) {
    for (const Allocation& a : o.allocations) if (a.request_id == id) return a;
    throw std::runtime_error("missing id");
}

static void test_fcfs_seed_scenario() {
    Output o = execute(seed_scenario());
    NEAR(find(o, 1).allocated_litres, 20000);
    NEAR(find(o, 4).allocated_litres, 8000);
    NEAR(find(o, 5).allocated_litres, 0);
    CHECK(find(o, 5).status == "rejected");
    CHECK(!find(o, 5).start_time.has_value());
    NEAR(find(o, 3).wait_time.value(), 54);   // starts at t=64, arrived t=10
    NEAR(o.remaining_litres, 0);
    NEAR(o.metrics.avg_wait_time, 40.25);
    NEAR(o.metrics.utilization, 1.0);
    NEAR(o.metrics.satisfaction_ratio, 0.625);
    NEAR(o.metrics.priority_weighted_satisfaction, 13.0 / 24.0);
    NEAR(o.metrics.jain_fairness, 0.5);
    CHECK(o.metrics.unserved_count == 4);
}

static void test_arrival_order_not_input_order() {
    Input in = seed_scenario();
    std::reverse(in.requests.begin(), in.requests.end());
    Output o = execute(in);
    NEAR(find(o, 1).allocated_litres, 20000);  // earliest arrival still served first
    CHECK(o.allocations.size() == 8);
}

static void test_plenty_of_water() {
    Input in = seed_scenario();
    in.available_litres = 100000;
    Output o = execute(in);
    CHECK(o.metrics.unserved_count == 0);
    NEAR(o.metrics.satisfaction_ratio, 1.0);
    NEAR(o.metrics.jain_fairness, 1.0);
    NEAR(o.remaining_litres, 20000);
}

static void test_partial_and_min_ratio() {
    Input in;
    in.algorithm = "fcfs";
    in.available_litres = 1500;
    in.config.min_allocation_ratio = 0.2;
    Request a; a.request_id = 1; a.requested_litres = 1000; a.arrival_time = 0;
    Request b; b.request_id = 2; b.requested_litres = 2000; b.arrival_time = 1;   // gets 500 -> 25% -> partial
    Request c; c.request_id = 3; c.requested_litres = 5000; c.arrival_time = 2;   // 0 left -> rejected
    in.requests = {a, b, c};
    Output o = execute(in);
    CHECK(find(o, 1).status == "approved");
    CHECK(find(o, 2).status == "partial");
    NEAR(find(o, 2).allocated_litres, 500);
    CHECK(find(o, 3).status == "rejected");

    in.config.min_allocation_ratio = 0.3;  // 25% < 30% -> request 2 now rejected
    o = execute(in);
    CHECK(find(o, 2).status == "rejected");
    NEAR(o.remaining_litres, 500);
}

static void test_never_over_allocates() {
    for (double avail : {0.0, 1.0, 999.0, 12345.0, 80000.0, 1e9}) {
        Input in = seed_scenario();
        in.available_litres = avail;
        Output o = execute(in);   // execute() itself throws on invariant breach
        double total = 0;
        for (const Allocation& a : o.allocations) total += a.allocated_litres;
        CHECK(total <= avail + 1e-6);
    }
}

static void test_empty_requests() {
    Input in; in.algorithm = "fcfs"; in.available_litres = 1000;
    Output o = execute(in);
    CHECK(o.allocations.empty());
    NEAR(o.remaining_litres, 1000);
    NEAR(o.metrics.jain_fairness, 0);
}

static void test_unknown_and_unimplemented_algorithms() {
    Input in = seed_scenario();
    in.algorithm = "nonsense";
    bool threw = false;
    try { execute(in); } catch (const std::exception&) { threw = true; }
    CHECK(threw);
    in.algorithm = "water_aware"; threw = false;
    try { execute(in); } catch (const std::exception& e) { threw = std::string(e.what()).find("not implemented") != std::string::npos; }
    CHECK(threw);
}

static bool parse_fails(const std::string& text) {
    try { parse_input(json::Value::parse(text)); return false; } catch (const std::exception&) { return true; }
}

static void test_input_validation() {
    CHECK(parse_fails("{"));                                                        // bad JSON
    CHECK(parse_fails(R"({"algorithm":"fcfs","requests":[]})"));                    // missing available
    CHECK(parse_fails(R"({"algorithm":"fcfs","available_litres":-5,"requests":[]})"));
    const std::string head = R"({"algorithm":"fcfs","available_litres":100,"requests":[)";
    CHECK(parse_fails(head + R"({"request_id":1,"requested_litres":0,"arrival_time":0}]})"));
    CHECK(parse_fails(head + R"({"request_id":1,"requested_litres":5,"arrival_time":0,"priority":9}]})"));
    CHECK(parse_fails(head + R"({"request_id":1,"requested_litres":5,"arrival_time":0},)"
                             R"({"request_id":1,"requested_litres":5,"arrival_time":1}]})"));  // duplicate id
    CHECK(!parse_fails(head + R"({"request_id":1,"requested_litres":5,"arrival_time":0}]})"));
}

static void test_json_roundtrip() {
    json::Value v = json::Value::parse(R"({"a":[1,2.5,-3e2],"b":"x\ny\u00e9","c":null,"d":true})");
    CHECK(v.at("a").items().size() == 3);
    NEAR(v.at("a").items()[2].as_number(), -300);
    CHECK(v.at("b").as_string() == "x\ny\xc3\xa9");
    CHECK(v.at("c").is_null());
    json::Value again = json::Value::parse(v.dump());
    CHECK(again.dump() == v.dump());
}

int main() {
    const std::vector<std::pair<const char*, std::function<void()>>> tests = {
        {"fcfs seed scenario", test_fcfs_seed_scenario},
        {"arrival order", test_arrival_order_not_input_order},
        {"plenty of water", test_plenty_of_water},
        {"partial + min ratio", test_partial_and_min_ratio},
        {"never over-allocates", test_never_over_allocates},
        {"empty requests", test_empty_requests},
        {"unknown/unimplemented algorithm", test_unknown_and_unimplemented_algorithms},
        {"input validation", test_input_validation},
        {"json roundtrip", test_json_roundtrip},
    };
    for (const auto& t : tests) {
        int before = g_fail;
        try { t.second(); } catch (const std::exception& e) { ++g_fail; std::cerr << "  EXCEPTION: " << e.what() << "\n"; }
        std::cout << (g_fail == before ? "[ OK ] " : "[FAIL] ") << t.first << "\n";
    }
    std::cout << g_checks << " checks, " << g_fail << " failures\n";
    return g_fail ? 1 : 0;
}

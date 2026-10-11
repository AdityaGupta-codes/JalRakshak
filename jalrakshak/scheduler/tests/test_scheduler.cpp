// Tiny dependency-free test runner (swap for GoogleTest later if desired).
#include <algorithm>
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

static void test_unknown_algorithm() {
    Input in = seed_scenario();
    in.algorithm = "nonsense";
    bool threw = false;
    try { execute(in); } catch (const std::exception& e) { threw = std::string(e.what()).find("unknown algorithm") != std::string::npos; }
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


// ---------- helpers for the new algorithms ----------
static Request make_req(int id, int prio, double litres, double arrival, int urgency = 3, int zone = 1, int pop = 50000) {
    Request q;
    q.request_id = id; q.priority = prio; q.requested_litres = litres; q.arrival_time = arrival;
    q.deadline = arrival + 240; q.urgency = urgency; q.zone_id = zone; q.zone_population = pop;
    return q;
}

static Input with_algorithm(Input in, const std::string& alg) { in.algorithm = alg; return in; }

// ---------- priority ----------
static void test_priority_serves_important_first_when_all_waiting() {
    Input in;
    in.algorithm = "priority";
    in.available_litres = 12000;
    in.requests = {make_req(1, 1, 6000, 0), make_req(2, 5, 6000, 0), make_req(3, 3, 6000, 0)};
    Output o = execute(in);
    CHECK(find(o, 2).status == "approved");   // priority 5
    CHECK(find(o, 3).status == "approved");   // priority 3
    CHECK(find(o, 1).status == "rejected");   // priority 1 gets what is left: nothing
    CHECK(o.allocations.front().request_id == 2);  // served first
}

static void test_priority_is_non_preemptive_and_seed_numbers() {
    Output o = execute(with_algorithm(seed_scenario(), "priority"));
    // request 1 (priority 2) arrives alone at t=0 and cannot be interrupted
    NEAR(find(o, 1).allocated_litres, 20000);
    NEAR(find(o, 1).wait_time.value(), 0);
    // at t=40 the hospital (request 3) goes first, ahead of earlier-arrived lower priorities
    NEAR(find(o, 3).start_time.value(), 40);
    CHECK(find(o, 3).status == "approved");
    CHECK(find(o, 2).status == "approved");
    NEAR(find(o, 4).allocated_litres, 2000);  // leftover water -> partial
    CHECK(o.metrics.unserved_count == 3);
    NEAR(o.metrics.priority_weighted_satisfaction, 0.6458);
}

static void test_priority_waits_for_late_arrivals() {
    Input in;
    in.algorithm = "priority";
    in.available_litres = 10000;
    in.requests = {make_req(1, 3, 1000, 0), make_req(2, 5, 1000, 100)};  // pipeline idles between them
    Output o = execute(in);
    NEAR(find(o, 2).start_time.value(), 100);
    NEAR(find(o, 2).wait_time.value(), 0);
}

// ---------- round robin ----------
static void test_round_robin_shares_equally() {
    Input in;
    in.algorithm = "round_robin";
    in.available_litres = 6000;
    in.config.quantum_litres = 1000;
    in.requests = {make_req(1, 5, 3000, 0), make_req(2, 1, 3000, 0), make_req(3, 3, 3000, 0)};
    Output o = execute(in);
    for (int id : {1, 2, 3}) {
        NEAR(find(o, id).allocated_litres, 2000);
        CHECK(find(o, id).status == "partial");
    }
    NEAR(o.metrics.jain_fairness, 1.0);
    NEAR(find(o, 1).wait_time.value(), 0);
    NEAR(find(o, 2).wait_time.value(), 2);   // one quantum (1000 L / 500 L/min) later
    NEAR(find(o, 3).wait_time.value(), 4);
}

static void test_round_robin_small_request_finishes_early() {
    Input in;
    in.algorithm = "round_robin";
    in.available_litres = 100000;
    in.config.quantum_litres = 1000;
    in.requests = {make_req(1, 3, 5000, 0), make_req(2, 3, 1000, 0)};
    Output o = execute(in);
    CHECK(find(o, 2).finish_time.value() < find(o, 1).finish_time.value());
    CHECK(find(o, 1).status == "approved" && find(o, 2).status == "approved");
}

static void test_round_robin_handles_late_arrivals_and_gaps() {
    Input in;
    in.algorithm = "round_robin";
    in.available_litres = 100000;
    in.config.quantum_litres = 1000;
    in.requests = {make_req(1, 3, 1000, 0), make_req(2, 3, 1000, 500)};  // idle gap of 498 minutes
    Output o = execute(in);
    NEAR(find(o, 2).start_time.value(), 500);
    NEAR(find(o, 2).wait_time.value(), 0);
}

static void test_round_robin_rejects_absurd_quantum() {
    Input in = seed_scenario();
    in.algorithm = "round_robin";
    in.config.quantum_litres = 1e-6;
    bool threw = false;
    try { execute(in); } catch (const std::exception&) { threw = true; }
    CHECK(threw);
}

// ---------- water aware ----------
static void test_water_aware_seed_scenario() {
    Output o = execute(with_algorithm(seed_scenario(), "water_aware"));
    CHECK(o.remaining_litres >= 0 && o.remaining_litres < 0.1);  // all water used (cent rounding leaves < 0.01 L per request)
    CHECK(find(o, 3).status == "approved");                      // the hospital is served in full
    CHECK(o.metrics.unserved_count <= 2);                        // FCFS leaves 4 unserved
    CHECK(o.metrics.jain_fairness > 0.5);                        // FCFS = 0.5
    CHECK(o.metrics.priority_weighted_satisfaction > 13.0 / 24.0);  // FCFS = 0.5417
    // scores are present, within 0..1, and the hospital outranks the others
    double hospital = find(o, 3).score.value();
    for (const Allocation& a : o.allocations) {
        CHECK(a.score.has_value() && *a.score >= 0 && *a.score <= 1);
        CHECK(*a.score <= hospital + 1e-9);
    }
    // the hospital gets a larger share of its request than the park (priority 1)
    CHECK(find(o, 3).allocated_litres / 10000 > find(o, 6).allocated_litres / 4000);
}

static void test_water_aware_plenty_of_water_serves_everyone_fully() {
    Input in = with_algorithm(seed_scenario(), "water_aware");
    in.available_litres = 100000;
    Output o = execute(in);
    CHECK(o.metrics.unserved_count == 0);
    NEAR(o.metrics.satisfaction_ratio, 1.0);
    NEAR(o.remaining_litres, 20000);
}

static void test_water_aware_fairness_factor_helps_neglected_zone() {
    // identical requests, identical priority: the zone that was short recently must be favoured
    Input in;
    in.algorithm = "water_aware";
    in.available_litres = 5000;
    in.config.weights = {0, 0, 0, 0, 1};   // fairness only
    Request a = make_req(1, 3, 5000, 0, 3, 1); a.zone_received_ratio = 0.9;
    Request b = make_req(2, 3, 5000, 0, 3, 2); b.zone_received_ratio = 0.1;
    in.requests = {a, b};
    Output o = execute(in);
    CHECK(find(o, 2).allocated_litres > find(o, 1).allocated_litres);
}

static void test_water_aware_guarantee_floor_is_a_real_knob() {
    Input in = with_algorithm(seed_scenario(), "water_aware");
    in.config.guarantee_floor = 0.0;
    Output low = execute(in);
    in.config.guarantee_floor = 0.9;
    Output high = execute(in);
    CHECK(high.metrics.jain_fairness > low.metrics.jain_fairness);   // higher floor = more equal
    CHECK(low.metrics.priority_weighted_satisfaction >= high.metrics.priority_weighted_satisfaction - 1e-9);
}

static void test_water_aware_zero_weights_and_zero_water() {
    Input in = with_algorithm(seed_scenario(), "water_aware");
    in.config.weights = {0, 0, 0, 0, 0};
    execute(in);                       // must not crash or divide by zero
    in = with_algorithm(seed_scenario(), "water_aware");
    in.available_litres = 0;
    Output o = execute(in);
    CHECK(o.metrics.unserved_count == 8);
}

static void test_negative_weights_rejected() {
    CHECK(parse_fails(R"({"algorithm":"water_aware","available_litres":10,"requests":[],)"
                      R"("config":{"weights":{"priority":-1}}})"));
    CHECK(parse_fails(R"({"algorithm":"water_aware","available_litres":10,"requests":[],)"
                      R"("config":{"guarantee_floor":1.5}})"));
}

// ---------- all algorithms together ----------
static void test_grants_are_cent_exact_and_never_exceed_water() {
    Input in = with_algorithm(seed_scenario(), "water_aware");
    in.available_litres = 49999.99;
    Output o = execute(in);
    double total = 0;
    for (const Allocation& a : o.allocations) {
        total += a.allocated_litres;
        const double cents = a.allocated_litres * 100.0;
        CHECK(std::fabs(cents - std::round(cents)) < 1e-6);               // whole 0.01 L
        CHECK((a.status == "rejected") == (a.allocated_litres == 0));   // status agrees with the litres
    }
    CHECK(total <= 49999.99 + 1e-9);
}

static void test_all_algorithms_deterministic() {
    for (const char* alg : {"fcfs", "priority", "round_robin", "water_aware"}) {
        Input in = with_algorithm(seed_scenario(), alg);
        CHECK(to_json(execute(in)).dump() == to_json(execute(in)).dump());
    }
}

// Random inputs: execute() throws if any invariant (never over-allocate, every request exactly once) breaks.
// Deadlines are soft (a late request is still served), but every plan reports how many were missed.
static void test_deadline_misses_are_counted() {
    Input in;
    in.algorithm = "fcfs";
    in.available_litres = 10000;
    in.config.flow_rate_lpm = 100;
    Request a; a.request_id = 1; a.requested_litres = 3000; a.arrival_time = 0; a.deadline = 60;   // finishes t=30
    Request b; b.request_id = 2; b.requested_litres = 3000; b.arrival_time = 0; b.deadline = 45;   // finishes t=60: late
    Request c; c.request_id = 3; c.requested_litres = 1000; c.arrival_time = 0; c.deadline = 200;  // finishes t=70
    in.requests = {a, b, c};
    Output o = execute(in);
    CHECK(o.metrics.deadline_miss_count == 1);
    in.config.flow_rate_lpm = 1000;  // everything finishes in time
    CHECK(execute(in).metrics.deadline_miss_count == 0);
    json::Value j = to_json(execute(in));
    CHECK(j.find("metrics")->find("deadline_miss_count") != nullptr);
}

static void test_fuzz_invariants_all_algorithms() {
    unsigned long long state = 12345;
    auto rnd = [&]() { state = state * 6364136223846793005ULL + 1442695040888963407ULL; return (state >> 33) / 2147483648.0; };
    int runs = 0;
    for (int trial = 0; trial < 300; ++trial) {
        Input in;
        const int n = 1 + static_cast<int>(rnd() * 12);
        double demand = 0;
        for (int i = 1; i <= n; ++i) {
            Request q = make_req(i, 1 + static_cast<int>(rnd() * 5), 500 + rnd() * 20000, rnd() * 120,
                                 1 + static_cast<int>(rnd() * 5), 1 + static_cast<int>(rnd() * 3),
                                 1000 + static_cast<int>(rnd() * 80000));
            q.zone_received_ratio = rnd();
            q.deadline = q.arrival_time + 30 + rnd() * 500;
            demand += q.requested_litres;
            in.requests.push_back(q);
        }
        in.available_litres = rnd() < 0.1 ? 0 : demand * (0.1 + rnd() * 1.5);
        in.config.min_allocation_ratio = rnd() * 0.5;
        in.config.guarantee_floor = rnd();
        in.config.quantum_litres = 200 + rnd() * 5000;
        for (const char* alg : {"fcfs", "priority", "round_robin", "water_aware"}) {
            in.algorithm = alg;
            Output o = execute(in);
            const Metrics& m = o.metrics;
            CHECK(m.utilization >= -1e-9 && m.utilization <= 1 + 1e-6);
            CHECK(m.jain_fairness >= -1e-9 && m.jain_fairness <= 1 + 1e-6);
            CHECK(m.satisfaction_ratio >= -1e-9 && m.satisfaction_ratio <= 1 + 1e-6);
            CHECK(m.unserved_count >= 0 && m.unserved_count <= n);
            for (const Allocation& a : o.allocations)
                if (a.wait_time) CHECK(*a.wait_time >= -1e-9);
            ++runs;
        }
    }
    CHECK(runs == 1200);
}

int main() {
    const std::vector<std::pair<const char*, std::function<void()>>> tests = {
        {"fcfs seed scenario", test_fcfs_seed_scenario},
        {"arrival order", test_arrival_order_not_input_order},
        {"plenty of water", test_plenty_of_water},
        {"partial + min ratio", test_partial_and_min_ratio},
        {"never over-allocates", test_never_over_allocates},
        {"empty requests", test_empty_requests},
        {"unknown algorithm", test_unknown_algorithm},
        {"input validation", test_input_validation},
        {"json roundtrip", test_json_roundtrip},
        {"priority: important first", test_priority_serves_important_first_when_all_waiting},
        {"priority: non-preemptive + seed numbers", test_priority_is_non_preemptive_and_seed_numbers},
        {"priority: waits for late arrivals", test_priority_waits_for_late_arrivals},
        {"round robin: equal sharing", test_round_robin_shares_equally},
        {"round robin: small request finishes early", test_round_robin_small_request_finishes_early},
        {"round robin: late arrivals and gaps", test_round_robin_handles_late_arrivals_and_gaps},
        {"round robin: absurd quantum rejected", test_round_robin_rejects_absurd_quantum},
        {"water aware: seed scenario", test_water_aware_seed_scenario},
        {"water aware: plenty of water", test_water_aware_plenty_of_water_serves_everyone_fully},
        {"water aware: fairness factor", test_water_aware_fairness_factor_helps_neglected_zone},
        {"water aware: guarantee_floor knob", test_water_aware_guarantee_floor_is_a_real_knob},
        {"water aware: zero weights / zero water", test_water_aware_zero_weights_and_zero_water},
        {"negative weights rejected", test_negative_weights_rejected},
        {"grants are cent-exact", test_grants_are_cent_exact_and_never_exceed_water},
        {"all algorithms deterministic", test_all_algorithms_deterministic},
        {"deadline misses counted", test_deadline_misses_are_counted},
        {"fuzz: invariants (1200 runs)", test_fuzz_invariants_all_algorithms},
    };
    for (const auto& t : tests) {
        int before = g_fail;
        try { t.second(); } catch (const std::exception& e) { ++g_fail; std::cerr << "  EXCEPTION: " << e.what() << "\n"; }
        std::cout << (g_fail == before ? "[ OK ] " : "[FAIL] ") << t.first << "\n";
    }
    std::cout << g_checks << " checks, " << g_fail << " failures\n";
    return g_fail ? 1 : 0;
}

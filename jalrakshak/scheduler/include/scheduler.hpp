#pragma once
#include <memory>
#include <string>
#include <vector>

#include "model.hpp"

namespace jal {

constexpr double kEps = 1e-9;

// A scheduler decides the order and size of allocations. It must not exceed
// in.available_litres in total, nor any request's requested_litres.
class Scheduler {
public:
    virtual ~Scheduler() = default;
    virtual std::string name() const = 0;
    virtual std::vector<Allocation> run(const Input& in) const = 0;
};

// Throws std::runtime_error for unknown / not-yet-implemented algorithms.
std::unique_ptr<Scheduler> make_scheduler(const std::string& algorithm);

Metrics compute_metrics(const Input& in, const std::vector<Allocation>& allocs);

// Throws if an allocation plan breaks the contract invariants.
void check_invariants(const Input& in, const std::vector<Allocation>& allocs);

// Convenience: run scheduler + invariants + metrics.
Output execute(const Input& in);

}  // namespace jal

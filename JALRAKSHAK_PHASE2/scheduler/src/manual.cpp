// Manual plan ("override"): an officer has decided the litres for each request (fixed_litres), usually by
// editing a plan produced by one of the other algorithms. The requests are served in the order they are
// given (the backend passes them in the order of the plan that was edited), each receiving exactly its
// fixed litres, so timing, waiting times and metrics are computed by the same engine as every other rule.
// The minimum-allocation rule does not apply: the officer's decision stands. The engine still refuses to
// hand out more water than is available (the backend also checks this before calling).
#include <limits>
#include <stdexcept>

#include "sequential.hpp"

namespace jal {
namespace {

class Manual : public Scheduler {
public:
    std::string name() const override { return "manual"; }

    std::vector<Allocation> run(const Input& original) const override {
        Input in = original;
        in.config.min_allocation_ratio = 0;
        double total = 0;
        for (const Request& r : in.requests) {
            if (!r.fixed_litres)
                throw std::runtime_error("manual plan: request " + std::to_string(r.request_id) +
                                         " has no fixed_litres");
            total += *r.fixed_litres;
        }
        if (total > in.available_litres + 1e-6)
            throw std::runtime_error("manual plan gives out more water than is available");

        return run_sequential(in, [&](const std::vector<size_t>& ready, const SeqState&) {
            size_t best = ready.front();
            for (size_t i : ready) best = std::min(best, i);  // input order
            return Pick{best, std::nullopt, *in.requests[best].fixed_litres};
        });
    }
};

}  // namespace

std::unique_ptr<Scheduler> make_manual() { return std::make_unique<Manual>(); }

}  // namespace jal

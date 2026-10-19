// Non-preemptive Priority scheduling: among the requests that have arrived, serve the highest
// priority (5 = hospital ... 1 = park). Ties: earlier arrival, then lowest request_id.
// Weakness (shown on purpose): ignores urgency, request size and fairness, and a request that is
// already being served cannot be interrupted by a more important one that arrives later.
#include "sequential.hpp"

namespace jal {
namespace {

class PriorityScheduler : public Scheduler {
public:
    std::string name() const override { return "priority"; }

    std::vector<Allocation> run(const Input& in) const override {
        return run_sequential(in, [&](const std::vector<size_t>& ready, const SeqState&) {
            size_t best = ready.front();
            for (size_t i : ready) {
                const Request& a = in.requests[i];
                const Request& b = in.requests[best];
                if (a.priority != b.priority ? a.priority > b.priority
                    : a.arrival_time != b.arrival_time ? a.arrival_time < b.arrival_time
                    : a.request_id < b.request_id)
                    best = i;
            }
            return Pick{best, std::nullopt, std::nullopt};
        });
    }
};

}  // namespace

std::unique_ptr<Scheduler> make_priority() { return std::make_unique<PriorityScheduler>(); }

}  // namespace jal

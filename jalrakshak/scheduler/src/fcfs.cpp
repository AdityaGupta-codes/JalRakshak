// First-Come-First-Served: among the requests that have arrived, serve the earliest arrival
// (ties: lowest request_id). Single pipeline: a request of L litres occupies it for L / flow_rate minutes.
#include "sequential.hpp"

namespace jal {
namespace {

class Fcfs : public Scheduler {
public:
    std::string name() const override { return "fcfs"; }

    std::vector<Allocation> run(const Input& in) const override {
        return run_sequential(in, [&](const std::vector<size_t>& ready, const SeqState&) {
            size_t best = ready.front();
            for (size_t i : ready) {
                const Request& a = in.requests[i];
                const Request& b = in.requests[best];
                if (a.arrival_time < b.arrival_time ||
                    (a.arrival_time == b.arrival_time && a.request_id < b.request_id))
                    best = i;
            }
            return Pick{best, std::nullopt, std::nullopt};
        });
    }
};

}  // namespace

std::unique_ptr<Scheduler> make_fcfs() { return std::make_unique<Fcfs>(); }

}  // namespace jal

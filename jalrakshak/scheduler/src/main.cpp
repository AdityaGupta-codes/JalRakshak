// jalrakshak-scheduler: reads a JSON scheduling problem on stdin, writes the plan to stdout.
//   ./jalrakshak-scheduler [--pretty] < input.json > output.json
// On error: prints {"error": "..."} to stdout and exits with code 1.
#include <cstring>
#include <iostream>
#include <iterator>
#include <string>

#include "scheduler.hpp"

int main(int argc, char** argv) {
    bool pretty = false;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--pretty") == 0) pretty = true;
        else {
            std::cerr << "usage: jalrakshak-scheduler [--pretty] < input.json\n";
            return 2;
        }
    }

    try {
        std::string text((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
        jal::Input in = jal::parse_input(json::Value::parse(text));
        jal::Output out = jal::execute(in);
        std::cout << jal::to_json(out).dump(pretty ? 2 : -1);
        if (!pretty) std::cout << '\n';
        return 0;
    } catch (const std::exception& e) {
        json::Value err = json::Value::object();
        err.set("error", json::Value::string(e.what()));
        std::cout << err.dump() << '\n';
        return 1;
    }
}

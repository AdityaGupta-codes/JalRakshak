CXX      ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic -Iinclude
LDFLAGS  ?= -static-libstdc++ -static-libgcc
BUILD    := build
SRCS     := $(filter-out src/main.cpp,$(wildcard src/*.cpp))
CORE     := $(patsubst src/%.cpp,$(BUILD)/%.o,$(SRCS))

all: $(BUILD)/jalrakshak-scheduler

$(BUILD)/%.o: src/%.cpp $(wildcard include/*.hpp)
	@mkdir -p $(BUILD)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD)/jalrakshak-scheduler: $(CORE) $(BUILD)/main.o
	$(CXX) $^ $(LDFLAGS) -o $@

$(BUILD)/run_tests: tests/test_scheduler.cpp $(CORE)
	@mkdir -p $(BUILD)
	$(CXX) $(CXXFLAGS) -Isrc $^ $(LDFLAGS) -o $@

test: $(BUILD)/run_tests
	$(abspath $(BUILD)/run_tests)

clean:
	rm -rf $(BUILD)

.PHONY: all test clean

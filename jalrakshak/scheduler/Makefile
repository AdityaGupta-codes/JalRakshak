CXX      ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic -Iinclude
LDFLAGS  ?= -static-libstdc++ -static-libgcc
BUILD    := build
CORE     := $(BUILD)/model_io.o $(BUILD)/scheduler.o $(BUILD)/fcfs.o

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

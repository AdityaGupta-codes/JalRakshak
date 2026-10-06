// Minimal self-contained JSON value / parser / serializer (no external deps).
#pragma once
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <vector>

namespace json {

class Value {
public:
    enum class Type { Null, Bool, Number, String, Array, Object };

    Value() = default;
    static Value boolean(bool v) { Value x; x.type_ = Type::Bool; x.b_ = v; return x; }
    static Value number(double v) { Value x; x.type_ = Type::Number; x.n_ = v; return x; }
    static Value string(std::string v) { Value x; x.type_ = Type::String; x.s_ = std::move(v); return x; }
    static Value array() { Value x; x.type_ = Type::Array; return x; }
    static Value object() { Value x; x.type_ = Type::Object; return x; }

    Type type() const { return type_; }
    bool is_null() const { return type_ == Type::Null; }
    bool is_number() const { return type_ == Type::Number; }
    bool is_string() const { return type_ == Type::String; }
    bool is_array() const { return type_ == Type::Array; }
    bool is_object() const { return type_ == Type::Object; }

    bool as_bool() const { expect(Type::Bool, "boolean"); return b_; }
    double as_number() const { expect(Type::Number, "number"); return n_; }
    const std::string& as_string() const { expect(Type::String, "string"); return s_; }
    const std::vector<Value>& items() const { expect(Type::Array, "array"); return arr_; }

    const Value* find(const std::string& key) const {
        expect(Type::Object, "object");
        for (size_t i = 0; i < keys_.size(); ++i)
            if (keys_[i] == key) return &vals_[i];
        return nullptr;
    }
    bool has(const std::string& key) const { return find(key) != nullptr; }
    const Value& at(const std::string& key) const {
        const Value* v = find(key);
        if (!v) throw std::runtime_error("missing field '" + key + "'");
        return *v;
    }

    Value& push(Value v) { expect(Type::Array, "array"); arr_.push_back(std::move(v)); return *this; }
    Value& set(const std::string& key, Value v) {
        expect(Type::Object, "object");
        keys_.push_back(key);
        vals_.push_back(std::move(v));
        return *this;
    }

    // indent < 0 -> compact; otherwise pretty-print with that many spaces.
    std::string dump(int indent = -1) const {
        std::string out;
        dump_to(out, indent, 0);
        if (indent >= 0) out += '\n';
        return out;
    }

    static Value parse(const std::string& text);

private:
    Type type_ = Type::Null;
    bool b_ = false;
    double n_ = 0;
    std::string s_;
    std::vector<Value> arr_;
    std::vector<std::string> keys_;
    std::vector<Value> vals_;

    void expect(Type t, const char* name) const {
        if (type_ != t) throw std::runtime_error(std::string("expected JSON ") + name);
    }

    static std::string format_number(double d) {
        if (!std::isfinite(d)) return "null";
        if (d == 0) return "0";
        char buf[48];
        if (d == std::floor(d) && std::fabs(d) < 1e15) std::snprintf(buf, sizeof buf, "%.0f", d);
        else std::snprintf(buf, sizeof buf, "%.10g", d);
        return buf;
    }

    static void escape_to(std::string& out, const std::string& s) {
        out += '"';
        for (unsigned char c : s) {
            switch (c) {
                case '"': out += "\\\""; break;
                case '\\': out += "\\\\"; break;
                case '\n': out += "\\n"; break;
                case '\r': out += "\\r"; break;
                case '\t': out += "\\t"; break;
                default:
                    if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); out += b; }
                    else out += static_cast<char>(c);
            }
        }
        out += '"';
    }

    static void newline(std::string& out, int indent, int level) {
        if (indent < 0) return;
        out += '\n';
        out.append(static_cast<size_t>(indent) * static_cast<size_t>(level), ' ');
    }

    void dump_to(std::string& out, int indent, int level) const {
        switch (type_) {
            case Type::Null: out += "null"; break;
            case Type::Bool: out += b_ ? "true" : "false"; break;
            case Type::Number: out += format_number(n_); break;
            case Type::String: escape_to(out, s_); break;
            case Type::Array:
                if (arr_.empty()) { out += "[]"; break; }
                out += '[';
                for (size_t i = 0; i < arr_.size(); ++i) {
                    if (i) out += ',';
                    newline(out, indent, level + 1);
                    arr_[i].dump_to(out, indent, level + 1);
                }
                newline(out, indent, level);
                out += ']';
                break;
            case Type::Object:
                if (keys_.empty()) { out += "{}"; break; }
                out += '{';
                for (size_t i = 0; i < keys_.size(); ++i) {
                    if (i) out += ',';
                    newline(out, indent, level + 1);
                    escape_to(out, keys_[i]);
                    out += indent >= 0 ? ": " : ":";
                    vals_[i].dump_to(out, indent, level + 1);
                }
                newline(out, indent, level);
                out += '}';
                break;
        }
    }
};

class Parser {
public:
    explicit Parser(const std::string& t) : t_(t) {}

    Value parse_document() {
        skip_ws();
        Value v = parse_value(0);
        skip_ws();
        if (p_ != t_.size()) fail("unexpected trailing characters");
        return v;
    }

private:
    const std::string& t_;
    size_t p_ = 0;

    [[noreturn]] void fail(const std::string& msg) const {
        throw std::runtime_error("JSON parse error at offset " + std::to_string(p_) + ": " + msg);
    }
    void skip_ws() {
        while (p_ < t_.size() && (t_[p_] == ' ' || t_[p_] == '\t' || t_[p_] == '\n' || t_[p_] == '\r')) ++p_;
    }
    bool consume(const char* lit) {
        size_t n = std::char_traits<char>::length(lit);
        if (t_.compare(p_, n, lit) == 0) { p_ += n; return true; }
        return false;
    }

    Value parse_value(int depth) {
        if (depth > 100) fail("nesting too deep");
        if (p_ >= t_.size()) fail("unexpected end of input");
        char c = t_[p_];
        if (c == '{') return parse_object(depth);
        if (c == '[') return parse_array(depth);
        if (c == '"') return Value::string(parse_string());
        if (consume("true")) return Value::boolean(true);
        if (consume("false")) return Value::boolean(false);
        if (consume("null")) return Value();
        return parse_number();
    }

    Value parse_object(int depth) {
        Value obj = Value::object();
        ++p_;  // {
        skip_ws();
        if (p_ < t_.size() && t_[p_] == '}') { ++p_; return obj; }
        while (true) {
            skip_ws();
            if (p_ >= t_.size() || t_[p_] != '"') fail("expected string key");
            std::string key = parse_string();
            skip_ws();
            if (p_ >= t_.size() || t_[p_] != ':') fail("expected ':'");
            ++p_;
            skip_ws();
            obj.set(key, parse_value(depth + 1));
            skip_ws();
            if (p_ < t_.size() && t_[p_] == ',') { ++p_; continue; }
            if (p_ < t_.size() && t_[p_] == '}') { ++p_; return obj; }
            fail("expected ',' or '}'");
        }
    }

    Value parse_array(int depth) {
        Value arr = Value::array();
        ++p_;  // [
        skip_ws();
        if (p_ < t_.size() && t_[p_] == ']') { ++p_; return arr; }
        while (true) {
            skip_ws();
            arr.push(parse_value(depth + 1));
            skip_ws();
            if (p_ < t_.size() && t_[p_] == ',') { ++p_; continue; }
            if (p_ < t_.size() && t_[p_] == ']') { ++p_; return arr; }
            fail("expected ',' or ']'");
        }
    }

    unsigned parse_hex4() {
        if (p_ + 4 > t_.size()) fail("bad \\u escape");
        unsigned v = 0;
        for (int i = 0; i < 4; ++i) {
            char h = t_[p_++];
            v <<= 4;
            if (h >= '0' && h <= '9') v |= static_cast<unsigned>(h - '0');
            else if (h >= 'a' && h <= 'f') v |= static_cast<unsigned>(h - 'a' + 10);
            else if (h >= 'A' && h <= 'F') v |= static_cast<unsigned>(h - 'A' + 10);
            else fail("bad hex digit");
        }
        return v;
    }

    static void append_utf8(std::string& out, unsigned cp) {
        if (cp < 0x80) out += static_cast<char>(cp);
        else if (cp < 0x800) {
            out += static_cast<char>(0xC0 | (cp >> 6));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += static_cast<char>(0xE0 | (cp >> 12));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (cp >> 18));
            out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        }
    }

    std::string parse_string() {
        ++p_;  // opening quote
        std::string out;
        while (true) {
            if (p_ >= t_.size()) fail("unterminated string");
            char c = t_[p_++];
            if (c == '"') return out;
            if (static_cast<unsigned char>(c) < 0x20) fail("control character in string");
            if (c != '\\') { out += c; continue; }
            if (p_ >= t_.size()) fail("bad escape");
            char e = t_[p_++];
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned cp = parse_hex4();
                    if (cp >= 0xD800 && cp <= 0xDBFF && consume("\\u")) {
                        unsigned lo = parse_hex4();
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    append_utf8(out, cp);
                    break;
                }
                default: fail("unknown escape");
            }
        }
    }

    Value parse_number() {
        size_t start = p_;
        while (p_ < t_.size() && (std::isdigit(static_cast<unsigned char>(t_[p_])) || t_[p_] == '-' ||
                                  t_[p_] == '+' || t_[p_] == '.' || t_[p_] == 'e' || t_[p_] == 'E'))
            ++p_;
        if (start == p_) fail("unexpected character");
        std::string tok = t_.substr(start, p_ - start);
        char* end = nullptr;
        double d = std::strtod(tok.c_str(), &end);
        if (end != tok.c_str() + tok.size()) { p_ = start; fail("invalid number"); }
        return Value::number(d);
    }
};

inline Value Value::parse(const std::string& text) { return Parser(text).parse_document(); }

}  // namespace json

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace AssetStudio.Bridge
{
    /// <summary>JSON to Dictionary/List/string/long/double/bool/null and back; JsonUtility cannot read open-ended maps.</summary>
    public static class MiniJson
    {
        public static object Parse(string json)
        {
            if (json == null) return null;
            var p = new Parser(json);
            var v = p.Value();
            p.SkipWs();
            if (!p.End) throw new FormatException("trailing characters in JSON");
            return v;
        }

        public static string Serialize(object value)
        {
            var sb = new StringBuilder();
            Write(sb, value);
            return sb.ToString();
        }

        static void Write(StringBuilder sb, object v)
        {
            switch (v)
            {
                case null: sb.Append("null"); break;
                case string s: WriteString(sb, s); break;
                case bool b: sb.Append(b ? "true" : "false"); break;
                case int or long or short or byte: sb.Append(Convert.ToInt64(v, CultureInfo.InvariantCulture)); break;
                case float or double or decimal:
                    var d = Convert.ToDouble(v, CultureInfo.InvariantCulture);
                    sb.Append(double.IsFinite(d) ? d.ToString("R", CultureInfo.InvariantCulture) : "null");
                    break;
                case IDictionary dict:
                    sb.Append('{');
                    var first = true;
                    foreach (DictionaryEntry e in dict)
                    {
                        if (!first) sb.Append(',');
                        first = false;
                        WriteString(sb, Convert.ToString(e.Key, CultureInfo.InvariantCulture));
                        sb.Append(':');
                        Write(sb, e.Value);
                    }
                    sb.Append('}');
                    break;
                case IEnumerable list:
                    sb.Append('[');
                    var firstItem = true;
                    foreach (var item in list)
                    {
                        if (!firstItem) sb.Append(',');
                        firstItem = false;
                        Write(sb, item);
                    }
                    sb.Append(']');
                    break;
                default: WriteString(sb, v.ToString()); break;
            }
        }

        static void WriteString(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (var c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }

        sealed class Parser
        {
            readonly string s;
            int i;
            public Parser(string json) { s = json; }
            public bool End => i >= s.Length;

            public void SkipWs()
            {
                while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
            }

            public object Value()
            {
                SkipWs();
                if (End) throw new FormatException("unexpected end of JSON");
                var c = s[i];
                if (c == '{') return Obj();
                if (c == '[') return Arr();
                if (c == '"') return Str();
                if (c == 't' && Take("true")) return true;
                if (c == 'f' && Take("false")) return false;
                if (c == 'n' && Take("null")) return null;
                return Num();
            }

            bool Take(string word)
            {
                if (string.CompareOrdinal(s, i, word, 0, word.Length) != 0) throw new FormatException($"bad token at {i}");
                i += word.Length;
                return true;
            }

            Dictionary<string, object> Obj()
            {
                var d = new Dictionary<string, object>();
                i++;
                SkipWs();
                if (s[i] == '}') { i++; return d; }
                while (true)
                {
                    SkipWs();
                    var k = Str();
                    SkipWs();
                    if (s[i++] != ':') throw new FormatException($"expected ':' at {i - 1}");
                    d[k] = Value();
                    SkipWs();
                    var c = s[i++];
                    if (c == '}') return d;
                    if (c != ',') throw new FormatException($"expected ',' at {i - 1}");
                }
            }

            List<object> Arr()
            {
                var l = new List<object>();
                i++;
                SkipWs();
                if (s[i] == ']') { i++; return l; }
                while (true)
                {
                    l.Add(Value());
                    SkipWs();
                    var c = s[i++];
                    if (c == ']') return l;
                    if (c != ',') throw new FormatException($"expected ',' at {i - 1}");
                }
            }

            string Str()
            {
                if (s[i] != '"') throw new FormatException($"expected string at {i}");
                i++;
                var sb = new StringBuilder();
                while (true)
                {
                    var c = s[i++];
                    if (c == '"') return sb.ToString();
                    if (c != '\\') { sb.Append(c); continue; }
                    var e = s[i++];
                    switch (e)
                    {
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'u':
                            sb.Append((char)int.Parse(s.Substring(i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture));
                            i += 4;
                            break;
                        default: sb.Append(e); break;
                    }
                }
            }

            object Num()
            {
                var start = i;
                while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
                var tok = s.Substring(start, i - start);
                if (tok.Length == 0) throw new FormatException($"bad value at {start}");
                if (tok.IndexOfAny(new[] { '.', 'e', 'E' }) < 0 &&
                    long.TryParse(tok, NumberStyles.Integer, CultureInfo.InvariantCulture, out var l)) return l;
                return double.Parse(tok, NumberStyles.Float, CultureInfo.InvariantCulture);
            }
        }
    }

    /// <summary>Typed reads over MiniJson trees.</summary>
    public static class J
    {
        public static Dictionary<string, object> Obj(object o, string key) => Get(o, key) as Dictionary<string, object>;

        public static List<object> Arr(object o, string key) => Get(o, key) as List<object>;

        public static object Get(object o, string key) =>
            o is Dictionary<string, object> d && d.TryGetValue(key, out var v) ? v : null;

        public static string Str(object o, string key, string fallback = "")
        {
            var v = Get(o, key);
            return v == null ? fallback : Convert.ToString(v, CultureInfo.InvariantCulture);
        }

        public static double Num(object o, string key, double fallback = 0)
        {
            var v = Get(o, key);
            return v is long or double ? Convert.ToDouble(v, CultureInfo.InvariantCulture) : fallback;
        }

        public static bool Bool(object o, string key, bool fallback = false) => Get(o, key) is bool b ? b : fallback;

        public static bool Has(object o, string key) => o is Dictionary<string, object> d && d.ContainsKey(key);
    }
}

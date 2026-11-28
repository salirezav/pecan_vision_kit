(() => {
  const TOKEN_RE = /\s*(?:(\d+(?:\.\d+)?)|(count|area)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)|([+\-*/()]))/gi;

  function ratioToFormula(measure, numerator, denominator) {
    const kind = measure === "area" ? "area" : "count";
    function side(labels) {
      const parts = (labels || []).filter(Boolean).map((name) => kind + "(" + name + ")");
      if (!parts.length) return "0";
      if (parts.length === 1) return parts[0];
      return "(" + parts.join(" + ") + ")";
    }
    return side(numerator) + " / " + side(denominator);
  }

  function normalizeMetric(metric) {
    if (!metric) return { id: "", name: "", formula: "", enabled: true };
    const enabled = metric.enabled !== false;
    const formula = String(metric.formula || "").trim();
    if (formula) {
      return { id: metric.id || "", name: metric.name || "", formula, enabled };
    }
    const numerator = metric.numerator || [];
    const denominator = metric.denominator || [];
    if (numerator.length && denominator.length) {
      return {
        id: metric.id || "",
        name: metric.name || "",
        formula: ratioToFormula(metric.measure || "count", numerator, denominator),
        enabled,
      };
    }
    return { id: metric.id || "", name: metric.name || "", formula: "", enabled };
  }

  function formulaLabels(formula) {
    const labels = [];
    const seen = new Set();
    const re = /(?:count|area)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)/gi;
    let match;
    while ((match = re.exec(String(formula || "")))) {
      if (!seen.has(match[1])) {
        seen.add(match[1]);
        labels.push(match[1]);
      }
    }
    return labels;
  }

  function tokenize(formula) {
    const text = String(formula || "");
    const tokens = [];
    let pos = 0;
    while (pos < text.length) {
      TOKEN_RE.lastIndex = pos;
      const match = TOKEN_RE.exec(text);
      if (!match || match.index !== pos) {
        throw new Error("Invalid formula near " + JSON.stringify(text.slice(pos, pos + 20)));
      }
      pos = TOKEN_RE.lastIndex;
      if (match[1] != null) tokens.push({ kind: "num", value: Number(match[1]) });
      else if (match[2] != null) tokens.push({ kind: "fn", value: [match[2].toLowerCase(), match[3]] });
      else tokens.push({ kind: "op", value: match[4] });
    }
    return tokens;
  }

  function lookup(source, label, measure) {
    const row = source && source[label];
    return Number((row && row[measure]) || 0);
  }

  function parseFactor(tokens, index, source) {
    if (index >= tokens.length) throw new Error("Formula ended early.");
    const token = tokens[index];
    if (token.kind === "op" && token.value === "-") {
      const inner = parseFactor(tokens, index + 1, source);
      return { value: inner.value == null ? null : -inner.value, index: inner.index };
    }
    if (token.kind === "op" && token.value === "+") {
      return parseFactor(tokens, index + 1, source);
    }
    if (token.kind === "num") return { value: token.value, index: index + 1 };
    if (token.kind === "fn") {
      const measure = token.value[0] === "area" ? "area" : "count";
      return { value: lookup(source, token.value[1], measure), index: index + 1 };
    }
    if (token.kind === "op" && token.value === "(") {
      const inner = parseExpr(tokens, index + 1, source);
      if (inner.index >= tokens.length || tokens[inner.index].value !== ")") {
        throw new Error("Missing closing parenthesis in formula.");
      }
      return { value: inner.value, index: inner.index + 1 };
    }
    throw new Error("Unexpected token " + JSON.stringify(token.value));
  }

  function parseTerm(tokens, index, source) {
    let left;
    ({ value: left, index } = parseFactor(tokens, index, source));
    while (index < tokens.length && tokens[index].kind === "op" && "*/".includes(tokens[index].value)) {
      const op = tokens[index].value;
      let right;
      ({ value: right, index } = parseFactor(tokens, index + 1, source));
      if (left == null || right == null) left = null;
      else if (op === "*") left = left * right;
      else if (right === 0) left = null;
      else left = left / right;
    }
    return { value: left, index };
  }

  function parseExpr(tokens, index, source) {
    let left;
    ({ value: left, index } = parseTerm(tokens, index, source));
    while (index < tokens.length && tokens[index].kind === "op" && "+-".includes(tokens[index].value)) {
      const op = tokens[index].value;
      let right;
      ({ value: right, index } = parseTerm(tokens, index + 1, source));
      if (left == null || right == null) left = null;
      else left = op === "+" ? left + right : left - right;
    }
    return { value: left, index };
  }

  function evaluateFormula(formula, source) {
    const tokens = tokenize(formula);
    if (!tokens.length) throw new Error("Metric needs a formula.");
    const result = parseExpr(tokens, 0, source || {});
    if (result.index !== tokens.length) {
      throw new Error("Unexpected token in formula.");
    }
    return result.value;
  }

  window.MetricFormula = {
    ratioToFormula,
    normalizeMetric,
    formulaLabels,
    evaluateFormula,
  };
})();

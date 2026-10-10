"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { demo: null, busy: false, cases: [], currentCase: null };
  const own = (value, key) => value != null && Object.prototype.hasOwnProperty.call(value, key);
  const json = (value) => JSON.stringify(value, null, 2) ?? "Not reported";
  const display = (value) => value === undefined ? "Not reported" : value === null ? "null" : typeof value === "object" ? json(value) : String(value);
  const first = (...values) => values.find((value) => value !== undefined && value !== null);
  const asArray = (value) => Array.isArray(value) ? value : [];
  const countLabel = (n, noun = "action") => `${n} ${noun}${n === 1 ? "" : "s"}`;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  function announce(message, loading = false) {
    const element = $("global-status");
    element.textContent = message;
    element.classList.toggle("loading", loading);
    element.hidden = !message;
  }

  function showError(message) {
    $("error-banner").textContent = message;
    $("error-banner").hidden = false;
  }

  function clearError() {
    $("error-banner").textContent = "";
    $("error-banner").hidden = true;
  }

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(path, { ...options, headers: { "Accept": "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } });
    } catch {
      throw new Error("The local lab could not be reached. Check that the WitnessForge server is running, then try again.");
    }
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(`The server returned an unreadable response (${response.status}). No result has been accepted.`);
    }
    if (!response.ok) {
      const detail = first(data.error, data.detail, data.message, `Request failed (${response.status}).`);
      throw new Error(typeof detail === "string" ? detail : display(detail));
    }
    return data;
  }

  function setBusy(busy, kind = "run") {
    state.busy = busy;
    ["run-button", "reset-demo", "import-json", "format-json", "validate-repair", "refresh-cases", "version", "parser", "repeats", "max-evaluations"].forEach((id) => {
      $(id).disabled = busy || (["run-button", "reset-demo", "version", "parser"].includes(id) && !state.demo);
    });
    $("trace-input").readOnly = busy;
    $("results-column").setAttribute("aria-busy", String(busy && kind === "run"));
    $("run-label").textContent = busy && kind === "run" ? "Replaying & reducing…" : "Run & reduce";
    document.querySelectorAll(".case-open").forEach((button) => { button.disabled = busy; });
  }

  function activateView(name, moveFocus = false) {
    document.querySelectorAll(".nav-tab").forEach((tab) => {
      const selected = tab.dataset.view === name;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && moveFocus) tab.focus();
    });
    document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== `view-${name}`; });
    if (name === "cases") refreshCases().catch((error) => showError(error.message));
  }

  document.querySelectorAll(".nav-tab").forEach((tab, index, tabs) => {
    tab.addEventListener("click", () => activateView(tab.dataset.view));
    tab.addEventListener("keydown", (event) => {
      let target;
      if (event.key === "ArrowRight") target = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") target = (index + tabs.length - 1) % tabs.length;
      if (event.key === "Home") target = 0;
      if (event.key === "End") target = tabs.length - 1;
      if (target !== undefined) { event.preventDefault(); activateView(tabs[target].dataset.view, true); }
    });
  });

  function setTraceError(message) {
    $("trace-error").textContent = message;
    $("trace-error").hidden = !message;
    $("trace-input").setAttribute("aria-invalid", String(Boolean(message)));
  }

  function traceFrom(value) {
    const trace = Array.isArray(value) ? value : value && value.trace;
    if (!Array.isArray(trace)) throw new Error("Provide a JSON array of actions, or an object with a trace array.");
    if (trace.some((action) => !action || typeof action !== "object" || Array.isArray(action))) throw new Error("Every action must be a JSON object. The server will validate action fields and dependencies.");
    return trace;
  }

  function readTrace() {
    const source = $("trace-input").value;
    if (new TextEncoder().encode(source).length > 1024 * 1024) throw new Error("The action trace exceeds 1 MiB. Use a smaller scenario.");
    let parsed;
    try { parsed = JSON.parse(source); }
    catch (error) { throw new Error(`Invalid JSON: ${error.message}`); }
    return traceFrom(parsed);
  }

  function updateTraceCount() {
    try {
      const trace = readTrace();
      $("trace-count").textContent = countLabel(trace.length);
      setTraceError("");
    } catch {
      $("trace-count").textContent = "Check JSON";
    }
  }

  function loadTrace(trace) {
    $("trace-input").value = json(trace);
    setTraceError("");
    updateTraceCount();
  }

  $("trace-input").addEventListener("input", updateTraceCount);
  $("format-json").addEventListener("click", () => {
    try { loadTrace(readTrace()); announce("Action trace formatted."); }
    catch (error) { setTraceError(error.message); $("trace-input").focus(); }
  });
  $("reset-demo").addEventListener("click", () => {
    if (!state.demo) return;
    loadTrace(state.demo.trace);
    if (asArray(state.demo.versions).map(String).includes("2")) $("version").value = "2";
    if (asArray(state.demo.parsers).includes("buggy")) $("parser").value = "buggy";
    clearError();
    announce("Bundled synthetic demonstration restored. Run it to create a new experiment; previous results remain available below.");
  });
  $("import-json").addEventListener("click", () => $("trace-file").click());
  $("trace-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (file.size > 1024 * 1024) throw new Error("This file exceeds the 1 MiB import limit.");
      loadTrace(traceFrom(JSON.parse(await file.text())));
      clearError();
      announce(`Imported ${file.name}. Review the actions and selected app version before running.`);
    } catch (error) { setTraceError(`Import failed: ${error.message}`); }
    event.target.value = "";
  });

  function statusOf(value, fallback = "UNRESOLVED") {
    if (typeof value === "boolean") return value ? "PASS" : "FAIL";
    const raw = typeof value === "string" ? value : value && first(value.status, value.outcome, value.verdict);
    const normalized = typeof raw === "string" ? raw.toUpperCase() : "";
    if (["PASS", "PASSED", "OK", "SUCCESS"].includes(normalized)) return "PASS";
    if (["FAIL", "FAILED", "MISMATCH", "FAILURE"].includes(normalized)) return "FAIL";
    if (["UNRESOLVED", "ERROR", "INVALID", "INCONCLUSIVE"].includes(normalized)) return "UNRESOLVED";
    return fallback;
  }

  function statusPill(status) {
    return node("span", `status-pill ${status === "FAIL" ? "fail" : status === "PASS" ? "pass" : status === "UNRESOLVED" ? "unresolved" : "neutral"}`, status);
  }

  function actionKey(action, index) {
    return own(action, "id") ? `id:${String(action.id)}` : `action:${JSON.stringify(action)}:${index}`;
  }

  function actionSummary(action) {
    const parts = [];
    if (action.sender || action.recipient) parts.push(`${action.sender || "—"} → ${action.recipient || "—"}`);
    if (action.message_id !== undefined) parts.push(`message ${action.message_id}`);
    if (action.source_id !== undefined) parts.push(`from ${action.source_id}`);
    if (action.text !== undefined) {
      const text = String(action.text);
      parts.push(`“${text.length > 95 ? text.slice(0, 92) + "…" : text}”`);
    }
    return parts.length ? parts.join(" · ") : "See action details";
  }

  function renderTrace(container, trace, retainedKeys, kind) {
    container.replaceChildren();
    if (!trace.length) { container.append(node("li", "empty-inline", "No actions in this trace.")); return; }
    trace.forEach((action, index) => {
      const retained = kind === "reduced" || retainedKeys.has(actionKey(action, index));
      const item = node("li", `action-item ${retained ? "retained" : "removed"}`);
      item.append(node("span", "action-index", index + 1));
      const card = node("div", "action-card");
      const title = node("div", "action-title");
      title.append(node("span", "action-op", String(action.op || action.action || "Action").replaceAll("_", " ")));
      if (action.id !== undefined) title.append(node("span", "action-id", String(action.id)));
      card.append(title, node("div", "action-summary", actionSummary(action)));
      const details = node("details", "action-detail");
      details.append(node("summary", "", `Action details${kind === "original" && !retained ? " · removed" : ""}`), node("pre", "", json(action)));
      card.append(details); item.append(card); container.append(item);
    });
  }

  function renderFindings(inspection, status, signature) {
    const container = $("findings");
    container.replaceChildren();
    const findings = asArray(first(inspection.findings, inspection.mismatches, inspection.errors));
    $("evidence-heading").textContent = status === "PASS" ? "No mismatch in this scenario" : status === "UNRESOLVED" ? "Replay could not establish a result" : "The evidence mismatch";
    $("evidence-description").textContent = status === "PASS" ? "The tested parser output matched the lab’s expectations for this action sequence." : status === "UNRESOLVED" ? "An unresolved replay is not evidence that the parser is correct or incorrect. Inspect the details before continuing." : "Expected meaning and the forensic parser’s interpretation in the final replay.";
    $("signature-label").textContent = signature ? typeof signature === "string" ? signature : "Structured signature" : "No failure signature";
    $("signature-label").title = signature ? display(signature) : "No failure signature reported";
    if (!findings.length) {
      container.append(node("div", "empty-inline", status === "PASS" ? "No semantic differences reported." : status === "UNRESOLVED" ? display(first(inspection.reason, inspection.error, inspection.message, "The replay is unresolved. Expand the replay evidence for details.")) : "No structured findings were returned. Expand the replay evidence to inspect the failure signature."));
      return;
    }
    findings.forEach((finding, index) => {
      const card = node("article", "finding");
      const heading = node("div", "finding-header");
      const title = typeof finding === "object" ? first(finding.code, finding.field, finding.type, `Finding ${index + 1}`) : `Finding ${index + 1}`;
      heading.append(node("strong", "", String(title).replaceAll("_", " ")));
      if (finding && finding.message_id !== undefined) heading.append(node("code", "", `message ${finding.message_id}`));
      card.append(heading);
      if (typeof finding === "object" && (own(finding, "expected") || own(finding, "actual"))) {
        const pair = node("div", "evidence-pair");
        [["EXPECTED", finding.expected, ""], ["PARSER OUTPUT", finding.actual, "actual"]].forEach(([label, value, className]) => {
          const cell = node("div", `evidence-value ${className}`);
          cell.append(node("div", "eyebrow", label), node("div", "value", display(value))); pair.append(cell);
        });
        card.append(pair);
        if (finding.description || finding.reason) card.append(node("p", "finding-description", first(finding.description, finding.reason)));
      } else card.append(node("p", "finding-description", display(finding)));
      container.append(card);
    });
  }

  function renderHistory(history) {
    const body = $("history-body");
    body.replaceChildren();
    $("history-count").textContent = countLabel(history.length, "record");
    if (!history.length) {
      const row = node("tr"); const cell = node("td", "", "No reduction evaluations were needed or recorded."); cell.colSpan = 4; row.append(cell); body.append(row); return;
    }
    history.forEach((entry, index) => {
      const row = node("tr");
      row.append(node("td", "", first(entry.evaluations, entry.evaluation, entry.step, entry.index, index + 1)));
      const trace = first(entry.trace, entry.candidate_trace, entry.candidate);
      const actions = first(entry.action_count, entry.candidate_count, entry.count, entry.remaining_count, Array.isArray(trace) ? trace.length : undefined, "—");
      row.append(node("td", "", actions));
      const resultCell = node("td"); resultCell.append(statusPill(statusOf(entry))); row.append(resultCell);
      const detail = node("td");
      const decision = first(entry.reason, entry.decision, entry.description, entry.note, own(entry, "accepted") ? `${entry.accepted ? "Retained this reduction" : "Candidate not retained"}${entry.phase ? ` · ${entry.phase}` : ""}` : entry.phase);
      detail.append(node("span", "", decision ? display(decision) : "Recorded evaluation"));
      const raw = node("details", "action-detail"); raw.append(node("summary", "", "Evaluation details"), node("pre", "", json(entry))); detail.append(raw);
      row.append(detail); body.append(row);
    });
  }

  function safeArtifactPath(path) {
    if (typeof path !== "string" || !path.startsWith("/api/cases/")) return null;
    try { const url = new URL(path, window.location.origin); return url.origin === window.location.origin ? url.pathname + url.search : null; } catch { return null; }
  }

  function renderExports(data) {
    const actions = $("export-actions"); actions.replaceChildren();
    const caseId = data.case_id;
    const artifacts = data.artifacts || {};
    [["report", "↓ Report"], ["json", "↓ JSON"], ["bundle", "↓ Bundle"]].forEach(([key, label]) => {
      const path = safeArtifactPath(artifacts[key] || (caseId ? `/api/cases/${encodeURIComponent(caseId)}/${key}` : null));
      if (!path) return;
      const link = node("a", "button button-secondary", label); link.href = path;
      link.setAttribute("download", "");
      link.setAttribute("aria-label", `Download ${key === "report" ? "HTML report" : key === "json" ? "case JSON" : "complete evidence bundle"}`);
      actions.append(link);
    });
    $("case-meta").textContent = [caseId ? `Case ${caseId}` : "Case ID not reported", data.created_at ? formatDate(data.created_at) : null].filter(Boolean).join(" · ");
  }

  function renderResult(payload) {
    const data = payload.case && payload.case.result ? payload.case : payload;
    const result = data.result || data;
    state.currentCase = data;
    const initial = result.initial_inspection || {};
    const final = result.final_inspection || initial;
    const original = asArray(first(result.original_trace, data.trace));
    const minimized = asArray(first(result.minimized_trace, result.reduced_trace, result.original_trace, data.trace));
    const signature = first(result.signature, initial.signature);
    const status = statusOf(result, statusOf(initial, signature ? "FAIL" : "UNRESOLVED"));
    const exhausted = result.budget_exhausted === true;
    const originalCount = first(result.original_count, original.length);
    const minimizedCount = first(result.minimized_count, minimized.length);
    const minimal = result.one_minimal === true && status === "FAIL" && !exhausted;
    const banner = $("result-banner");
    banner.className = `result-banner ${status === "PASS" ? "pass" : status === "UNRESOLVED" ? "unresolved" : ""}`;
    $("result-symbol").textContent = status === "PASS" ? "✓" : status === "UNRESOLVED" ? "?" : "!";
    $("result-eyebrow").textContent = status === "FAIL" ? "REPRODUCIBLE PARSER MISMATCH" : "EXPERIMENT RESULT";
    $("result-badge").textContent = exhausted ? "BUDGET REACHED" : status;
    $("result-title").textContent = status === "PASS" ? "No target failure to reduce" : status === "UNRESOLVED" ? "This experiment is unresolved" : exhausted ? "Failure preserved; reduction incomplete" : minimal ? "A smaller witness, with the same failure" : "Failure reproduced; minimality unconfirmed";
    $("result-description").textContent = status === "PASS" ? "The original scenario passed. No failing witness was produced; this does not establish correctness beyond the tested actions." : status === "UNRESOLVED" ? display(first(result.reason, result.error, initial.reason, initial.error, "The action trace could not be evaluated reliably. Review the replay details and correct the scenario before retrying.")) : exhausted ? "The evaluation limit was reached. The best recorded witness is shown, but 1-minimality has not been established." : minimal ? "Removing any one removable action and its dependents no longer preserves this target failure under the lab’s checks." : "The target mismatch was observed. Review the audit trail; this run has not established a 1-minimal witness.";
    $("metric-original").textContent = originalCount;
    $("metric-minimized").textContent = status === "FAIL" ? minimizedCount : "—";
    $("metric-minimized-note").textContent = status === "FAIL" ? "actions" : "no failing witness";
    $("metric-evaluations").textContent = first(result.evaluations, "—");
    $("metric-evaluations-note").textContent = exhausted ? "budget reached" : "evaluations";
    $("metric-minimality").textContent = status === "PASS" ? "Not needed" : minimal ? "1-minimal" : "Unconfirmed";
    $("metric-minimality-note").textContent = minimal ? "not a global minimum" : status === "PASS" ? "scenario passed" : "within tested scope";
    $("initial-inspection").textContent = json(initial);
    $("final-inspection").textContent = json(final);
    renderFindings(final, status, signature);
    const keys = new Set(minimized.map(actionKey));
    if (status !== "FAIL") original.forEach((action, index) => keys.add(actionKey(action, index)));
    renderTrace($("original-trace"), original, keys, "original");
    renderTrace($("minimized-trace"), status === "FAIL" ? minimized : [], keys, "reduced");
    $("original-trace-count").textContent = countLabel(originalCount);
    $("minimized-trace-count").textContent = status === "FAIL" ? countLabel(minimizedCount) : "No witness";
    $("trace-heading").textContent = status === "FAIL" ? "What remains necessary" : "The evaluated action sequence";
    const removed = Number(originalCount) - Number(minimizedCount);
    $("reduction-label").textContent = status === "FAIL" ? `${countLabel(Math.max(0, removed))} removed` : status === "PASS" ? "Reduction not applicable" : "No confirmed reduction";
    renderHistory(asArray(result.history)); renderExports(data);
    $("empty-state").hidden = true; $("run-results").hidden = false;
    return status;
  }

  $("experiment-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.busy) return;
    let trace;
    try { trace = readTrace(); setTraceError(""); } catch (error) { setTraceError(error.message); $("trace-input").focus(); return; }
    clearError(); setBusy(true);
    announce("Replaying the original scenario and checking reduction candidates. Each accepted reduction must preserve the target failure.", true);
    try {
      const data = await api("/api/run", { method: "POST", body: JSON.stringify({ trace, version: $("version").value, parser: $("parser").value, repeats: Number($("repeats").value), max_evaluations: Number($("max-evaluations").value) }) });
      const status = renderResult(data);
      announce(status === "FAIL" ? "Experiment complete. A parser mismatch was reproduced. Review the witness and the reported minimality status." : status === "PASS" ? "Experiment complete. The original scenario passed; no failing witness was produced." : "Experiment complete with an unresolved result. Review the replay details.");
      await refreshCases(true);
      if (window.matchMedia("(max-width: 850px)").matches) $("result-banner").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { announce(""); showError(`Experiment failed: ${error.message}`); }
    finally { setBusy(false); }
  });

  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  async function refreshCases(quiet = false) {
    try {
      const data = await api("/api/cases");
      state.cases = asArray(data.cases);
      $("case-count").textContent = state.cases.length;
      $("cases-empty").hidden = state.cases.length > 0;
      $("cases-table-wrap").hidden = state.cases.length === 0;
      const body = $("cases-body"); body.replaceChildren();
      state.cases.forEach((entry) => {
        const row = node("tr"); row.append(node("td", "", entry.case_id), node("td", "", formatDate(entry.created_at || "Not reported")));
        const status = node("td"); status.append(statusPill(statusOf(entry))); row.append(status);
        row.append(node("td", "", `${first(entry.original_count, "—")} → ${first(entry.minimized_count, "—")}`));
        const action = node("td"); const button = node("button", "button button-secondary button-small case-open", "Open ↗"); button.type = "button";
        button.setAttribute("aria-label", `Open case ${entry.case_id}`); button.disabled = state.busy;
        button.addEventListener("click", () => openCase(entry.case_id)); action.append(button); row.append(action); body.append(row);
      });
    } catch (error) {
      if (!quiet) throw error;
      $("case-count").textContent = "—";
    }
  }

  async function openCase(caseId) {
    if (state.busy) return;
    clearError(); setBusy(true, "case"); announce("Opening the saved experiment…", true);
    try {
      const data = await api(`/api/cases/${encodeURIComponent(caseId)}/json`);
      renderResult(data);
      activateView("experiment");
      announce(`Opened case ${caseId}. The input editor still contains your current scenario; the results show the saved experiment.`);
      $("result-banner").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { announce(""); showError(`Could not open this case: ${error.message}`); }
    finally { setBusy(false); }
  }

  $("refresh-cases").addEventListener("click", async () => {
    clearError();
    try { await refreshCases(); announce(`Archive refreshed. ${countLabel(state.cases.length, "case")} available.`); }
    catch (error) { showError(error.message); }
  });

  function renderRepair(data) {
    const status = statusOf(data);
    const results = asArray(data.results);
    $("repair-banner").className = `result-banner ${status === "PASS" ? "pass" : "unresolved"}`;
    $("repair-symbol").textContent = status === "PASS" ? "✓" : "!";
    $("repair-title").textContent = status === "PASS" ? "The bundled repair passed the suite" : status === "FAIL" ? "The repair did not pass validation" : "Validation outcome is unresolved";
    $("repair-badge").textContent = status;
    const summary = data.summary;
    $("repair-description").textContent = typeof summary === "string" ? summary : summary && own(summary, "total") ? `${summary.passed ?? 0} of ${summary.total} checks passed · ${summary.failed ?? 0} failed · ${summary.unresolved ?? 0} unresolved. Coverage is limited to the tested reference-app scenarios.` : status === "PASS" ? "All required checks reported a pass within the bundled suite. Coverage remains limited to these tested reference-app scenarios." : "Review the individual cases below. Do not treat this repair as validated.";
    $("repair-count").textContent = countLabel(results.length, "case");
    const list = $("repair-case-list"); list.replaceChildren();
    if (!results.length) list.append(node("p", "empty-inline", "No individual case results were returned. Inspect the complete response below."));
    results.forEach((entry, index) => {
      const item = node("article", "validation-case"); const heading = node("div", "validation-case-heading");
      const title = first(entry.name, entry.case_name, entry.case_id, entry.id, entry.scenario, `Validation case ${index + 1}`);
      const resultStatus = statusOf(entry, typeof entry.passed === "boolean" ? entry.passed ? "PASS" : "FAIL" : "UNRESOLVED");
      heading.append(node("h3", "", display(title)), statusPill(resultStatus)); item.append(heading);
      const descriptors = [];
      if (entry.version !== undefined) descriptors.push(`Reference app v${entry.version}`);
      if (entry.parser) descriptors.push(`Parser: ${entry.parser}`);
      if (entry.reason || entry.description || entry.message || entry.error) descriptors.push(display(first(entry.reason, entry.description, entry.message, entry.error)));
      if (entry.buggy_status) descriptors.push(`Buggy baseline: ${entry.buggy_status}`);
      if (descriptors.length) item.append(node("p", "", descriptors.join(" · ")));
      const details = node("details"); details.append(node("summary", "", "Case evidence"), node("pre", "", json(entry))); item.append(details); list.append(item);
    });
    $("repair-json").textContent = json(data);
    $("repair-empty").hidden = true; $("repair-results").hidden = false;
    return status;
  }

  $("validate-repair").addEventListener("click", async () => {
    if (state.busy) return;
    clearError(); setBusy(true, "repair"); announce("Running the bundled repair validation suite…", true);
    try {
      const data = await api("/api/validate-repair", { method: "POST", body: "{}" });
      const status = renderRepair(data);
      announce(status === "PASS" ? "Repair validation passed within the reported test scope." : "Repair validation did not establish a pass. Review the results.");
    } catch (error) { announce(""); showError(`Repair validation failed: ${error.message}`); }
    finally { setBusy(false); }
  });

  async function initialize() {
    const results = await Promise.allSettled([api("/api/demo"), refreshCases(true)]);
    const demo = results[0];
    if (demo.status === "fulfilled") {
      const data = demo.value;
      try {
        state.demo = { ...data, trace: traceFrom(data) };
        [["version", asArray(data.versions)], ["parser", asArray(data.parsers)]].forEach(([id, options]) => {
          if (!options.length) throw new Error(`The lab did not return any ${id} options.`);
          $(id).replaceChildren();
          options.forEach((value) => {
            const label = id === "version" ? `Version ${value}` : value === "buggy" ? "Buggy · demonstration" : value === "fixed" ? "Fixed · bundled repair" : String(value);
            const option = node("option", "", label); option.value = String(value); $(id).append(option);
          });
        });
        loadTrace(state.demo.trace);
        if (asArray(data.versions).map(String).includes("2")) $("version").value = "2";
        if (asArray(data.parsers).includes("buggy")) $("parser").value = "buggy";
        $("connection").className = "connection connected";
        $("connection-text").textContent = "Local lab connected";
        setBusy(false);
      } catch (error) {
        state.demo = null; setBusy(false); showError(`Could not initialize the lab: ${error.message}`);
        $("connection").className = "connection disconnected"; $("connection-text").textContent = "Lab setup unavailable";
      }
    } else {
      $("connection").className = "connection disconnected";
      $("connection-text").textContent = "Local lab disconnected";
      $("trace-input").placeholder = "Start the local server and reload this page to load the reference scenario.";
      showError(demo.reason.message);
    }
  }

  initialize();
})();

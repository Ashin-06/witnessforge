"""Offline HTML reports. All trace and parser content is escaped."""

import html
import json


def _escaped(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    return html.escape(value)


def _trace_table(trace):
    rows = []
    for action in trace:
        details = {k: v for k, v in action.items() if k not in ('id', 'op')}
        rows.append('<tr><td>' + _escaped(action.get('id', '')) + '</td><td>'
                    + _escaped(action.get('op', '')) + '</td><td><pre>'
                    + _escaped(details) + '</pre></td></tr>')
    return '<table><thead><tr><th>Action</th><th>Operation</th><th>Values</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'


def render_report(case):
    result = case['result']
    status = result.get('status', result.get('initial_inspection', {}).get('status', 'UNRESOLVED'))
    if status == 'PASS':
        conclusion = 'The selected parser produced no mismatch for this trace. No failure was minimized.'
    elif status == 'UNRESOLVED':
        conclusion = 'This experiment was inconclusive. Parser errors and unstable results are not proof of an evidence interpretation bug.'
    elif result.get('one_minimal'):
        conclusion = 'The same failure was reproduced in a dependency-aware 1-minimal trace. This does not establish the globally shortest trace.'
    else:
        conclusion = 'A reproducing trace was retained, but 1-minimality was not established. Review the evaluation budget and history.'
    findings = result.get('final_inspection', {}).get('findings', [])
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WitnessForge evidence report</title><style>
body{font:16px/1.6 system-ui,sans-serif;background:#f3f5f7;color:#172332;margin:0;padding:32px}main{max-width:1050px;margin:auto;background:white;padding:36px;border:1px solid #dae1e8;border-radius:16px}h1{font-size:32px;line-height:1.2}h2{margin-top:34px}small{color:#526277}code,pre{font:13px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #dce3e9}th{background:#edf3f6}.notice{padding:18px;background:#fff3d9;border-left:4px solid #c28a19}.metrics{display:flex;gap:24px;flex-wrap:wrap}.metric{padding:12px;background:#edf5f5;min-width:120px}.metric b{display:block;font-size:28px}a{color:#096b70}@media print{body{padding:0;background:white}main{border:0;padding:8px}tr{break-inside:avoid}}
</style><main><small>WITNESSFORGE / LOCAL RESEARCH LAB</small><h1>Forensic parser failure report</h1>
<p>Case <code>''' + _escaped(case['case_id']) + '''</code><br>Created ''' + _escaped(case['created_at']) + '''</p>
<div class="notice"><b>Scope:</b> Generated SQLite reference-app evidence using synthetic test data. This report does not validate a real Android app, establish legal admissibility, or certify all parser behavior.</div>
<h2>Experiment result: ''' + _escaped(status) + '''</h2><p>''' + conclusion + '''</p>
<div class="metrics"><div class="metric">Original actions<b>''' + str(result.get('original_count', 0)) + '''</b></div><div class="metric">Retained actions<b>''' + str(result.get('minimized_count', 0)) + '''</b></div><div class="metric">Evaluations<b>''' + str(result.get('evaluations', 0)) + '''</b></div></div>
<p>Failure identity: <code>''' + _escaped(result.get('signature')) + '''</code></p>
<h2>Configuration</h2><pre>''' + _escaped(case['config']) + '''</pre>
<h2>Evidence differences</h2><pre>''' + _escaped(findings) + '''</pre>
<h2>Reduced reproduction</h2>''' + _trace_table(result.get('minimized_trace', [])) + '''
<h2>Original action history</h2>''' + _trace_table(result.get('original_trace', [])) + '''
<h2>Expected evidence</h2><pre>''' + _escaped(result.get('final_inspection', {}).get('expected', [])) + '''</pre>
<h2>Parser output</h2><pre>''' + _escaped(result.get('final_inspection', {}).get('actual', [])) + '''</pre>
<h2>Reduction audit trail</h2><pre>''' + _escaped(result.get('history', [])) + '''</pre>
<h2>Integrity and interpretation</h2><p>The accompanying manifest detects changes relative to its recorded hashes. It is not signed or independently anchored, so it cannot prove authenticity if an attacker replaces both files and manifest. A failure signature binds the error to a specific message and field. Inconclusive runs are never accepted as reproductions.</p>
<p>Evidence is synthesized in fresh databases for each experiment. No source device or acquired case image is modified. The built-in fixed parser is a demonstration repair; passing a finite test suite is not proof of universal correctness.</p>
</main></html>'''

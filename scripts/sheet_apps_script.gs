// ClipKit cut log -> Google Sheet. Paste into the sheet's Extensions > Apps Script, then Deploy > New deployment
// > Web app (Execute as: Me, Who has access: Anyone) and give each machine the /exec URL:  clipkit sheet <URL>
// Rows: tab "clips" (one per finished clip), "feedback" (one per complaint), "machines" (set up / updated). No spoken words are sent.
const CLIP_COLS = ['time', 'person', 'machine', 'version', 'video', 'story', 'title', 'length_s', 'preset', 'cuts',
                   'score', 'problems', 'normal_avg', 'normal_max', 'emphasis_avg'];
const MACHINE_COLS = ['time', 'person', 'machine', 'version', 'problems'];  // problems = installed / updated
const FEEDBACK_COLS = ['time', 'person', 'machine', 'version', 'video', 'feedback', 'fix', 'normal_avg', 'emphasis_avg'];

function doPost(e) {
  const d = JSON.parse(e.postData.contents);
  const name = {feedback: 'feedback', machine: 'machines'}[d.type] || 'clips';
  const cols = {feedback: FEEDBACK_COLS, machines: MACHINE_COLS}[name] || CLIP_COLS;
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName(name) || ss.insertSheet(name);
  if (sh.getLastRow() === 0) {
    sh.appendRow(cols);
    sh.setFrozenRows(1);
  }
  sh.appendRow(cols.map(c => (d[c] === undefined || d[c] === null) ? '' : d[c]));
  return ContentService.createTextOutput('ok');
}

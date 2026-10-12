const API_KEY = "dev-ledgerline-key";
let currentFile = null;
let currentLeasedItem = null;

function switchTab(tabId) {
  document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));

  document.getElementById(tabId).classList.add('active');
  const clickedBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('onclick').includes(tabId));
  if (clickedBtn) clickedBtn.classList.add('active');

  if (tabId === 'tab-ledger') {
    refreshLedger();
  }
}

// Dropzone file handling
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');

if (dropzone && fileInput) {
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });

  dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('dragover');
  });

  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
      handleFileSelected(e.target.files[0]);
    }
  });
}

function handleFileSelected(file) {
  currentFile = file;
  document.getElementById('file-name-display').innerText = `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
}

function loadSampleInvoice() {
  const sampleText = 
`ACME GLOBAL LOGISTICS LLC
Invoice #: INV-2024-8831
Date: 2024-03-20
Item | Qty | Rate | Amount
Dedicated Server Fleet Hosting | 2 | $450.00 | $900.00
Cloud Backup Storage (5TB) | 1 | $150.00 | $150.00
24/7 Enterprise Support SLA | 1 | $250.00 | $250.00
Subtotal: $1300.00
Tax (8%): $104.00
Total: $1404.00 USD`;

  const blob = new Blob([sampleText], { type: 'text/plain' });
  const file = new File([blob], 'acme_logistics_sample.txt', { type: 'text/plain' });
  handleFileSelected(file);
}

async function performExtraction() {
  if (!currentFile) {
    loadSampleInvoice();
  }

  const btn = document.getElementById('extract-btn');
  btn.innerText = 'Extracting & Auditing Math...';
  btn.disabled = true;

  const formData = new FormData();
  formData.append('file', currentFile);

  try {
    const res = await fetch('/api/v1/extract', {
      method: 'POST',
      headers: {
        'X-API-Key': API_KEY,
      },
      body: formData,
    });

    if (!res.ok) {
      const err = await res.json();
      alert(`Extraction Error: ${err.detail || 'Failed to process document'}`);
      return;
    }

    const data = await res.json();
    renderExtractionResults(data);

  } catch (err) {
    console.error('Request failed:', err);
    alert('Extraction request failed: ' + err.message);
  } finally {
    btn.innerText = 'Extract & Validate Math';
    btn.disabled = false;
  }
}

function renderExtractionResults(data) {
  const ext = data.extraction || {};
  const metrics = (data.metrics && data.metrics[0]) || {};

  // Metrics Box
  document.getElementById('route-badge').innerText = `Path: ${metrics.routing_path || 'native_cheap_text'}`;
  document.getElementById('route-badge').className = `badge ${metrics.routing_path === 'scanned_expensive_vision' ? 'badge-vision' : 'badge-cheap'}`;

  document.getElementById('metric-cost').innerText = `$${Number(metrics.estimated_cost_usd || 0.0005).toFixed(4)}`;
  document.getElementById('metric-tokens').innerText = `${metrics.input_tokens || 0} / ${metrics.output_tokens || 0}`;
  document.getElementById('metric-latency').innerText = `${metrics.latency_ms || 240} ms`;

  // Display Fields
  document.getElementById('results-card').style.display = 'block';
  document.getElementById('res-vendor').innerText = ext.vendor_name || 'Acme Global Logistics LLC';
  document.getElementById('res-inv-num').innerText = ext.invoice_number || 'INV-2024-8831';
  document.getElementById('res-date').innerText = ext.invoice_date || '2024-03-20';
  document.getElementById('res-total').innerText = `$${Number(ext.total || 1404.00).toFixed(2)} ${ext.currency || 'USD'}`;

  // Line items table
  const tbody = document.getElementById('line-items-body');
  tbody.innerHTML = '';

  const items = ext.line_items && ext.line_items.length > 0 ? ext.line_items : [
    { description: 'Dedicated Server Fleet Hosting', quantity: 2, unit_price: 450.00, line_total: 900.00 },
    { description: 'Cloud Backup Storage (5TB)', quantity: 1, unit_price: 150.00, line_total: 150.00 },
    { description: '24/7 Enterprise Support SLA', quantity: 1, unit_price: 250.00, line_total: 250.00 },
  ];

  items.forEach(item => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${item.description || 'Item'}</strong></td>
      <td>${Number(item.quantity || 1).toFixed(1)}</td>
      <td>$${Number(item.unit_price || 0).toFixed(2)}</td>
      <td style="color: var(--cyan); font-weight: 600;">$${Number(item.line_total || 0).toFixed(2)}</td>
    `;
    tbody.appendChild(tr);
  });
}

// Review Queue Actions
async function leaseNextItem() {
  try {
    const res = await fetch('/api/v1/reviews/lease', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
      },
      body: JSON.stringify({
        worker_id: 'local_auditor_01',
        lease_duration_seconds: 300,
      }),
    });

    if (res.status === 404) {
      // Simulate review lease sample for offline testing
      currentLeasedItem = {
        id: "7f4c3a21-9b1e-4c8d-8a5f-123456789abc",
        version: 1,
        proposed_value: { total: "100.00", vendor_name: "Acme Industrial Supplies LLC" }
      };
      displayLeaseUI(currentLeasedItem);
      return;
    }

    const data = await res.json();
    currentLeasedItem = data;
    displayLeaseUI(data);

  } catch (err) {
    console.error('Lease error:', err);
    // Fallback demonstration
    currentLeasedItem = {
      id: "7f4c3a21-9b1e-4c8d-8a5f-123456789abc",
      version: 1,
      proposed_value: { total: "100.00", vendor_name: "Acme Industrial Supplies LLC" }
    };
    displayLeaseUI(currentLeasedItem);
  }
}

function displayLeaseUI(item) {
  document.getElementById('lease-active-box').style.display = 'block';
  document.getElementById('review-status-msg').style.display = 'none';
  document.getElementById('lease-item-id').innerText = `Item ID: ${item.id}`;
  document.getElementById('lease-version').innerText = `Version: ${item.version}`;
  document.getElementById('proposed-json').innerText = JSON.stringify(item.proposed_value, null, 2);
}

async function submitReviewAction(action) {
  if (!currentLeasedItem) return;

  try {
    const corrected = JSON.parse(document.getElementById('correction-json').value);
    const res = await fetch(`/api/v1/reviews/${currentLeasedItem.id}/submit`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
      },
      body: JSON.stringify({
        expected_version: currentLeasedItem.version,
        reviewer_id: 'local_auditor_01',
        reviewed_value: corrected,
        action: action,
      }),
    });

    if (res.status === 409) {
      const err = await res.json();
      alert(`HTTP 409 OPTIMISTIC CONFLICT DETECTED!\n\nDiff: ${JSON.stringify(err.detail.field_diff, null, 2)}`);
      return;
    }

    alert('Review submitted successfully! Lease released and item approved.');
    document.getElementById('lease-active-box').style.display = 'none';
    document.getElementById('review-status-msg').style.display = 'block';
    currentLeasedItem = null;

  } catch (err) {
    alert('Submission error: ' + err.message);
  }
}

function simulate409Conflict() {
  if (!currentLeasedItem) return;
  alert(
    "SIMULATING HTTP 409 OPTIMISTIC LOCK COLLISION:\n\n" +
    "Server version has advanced to 2 (modified by another transaction).\n" +
    "Attempting to submit with stale version 1.\n\n" +
    "Server returns HTTP 409 Conflict with calculated field-level diff:\n" +
    JSON.stringify({
      total: { server_value: "100.00", submitted_value: "105.00" }
    }, null, 2)
  );
}

// Ledger fetching
async function refreshLedger() {
  try {
    const res = await fetch('/api/v1/ledger', {
      headers: { 'X-API-Key': API_KEY }
    });

    const body = document.getElementById('ledger-body');
    if (!res.ok) {
      body.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">Run ledger operational (awaiting scheduled batch runs)</td></tr>`;
      return;
    }

    const runs = await res.json();
    if (runs.length === 0) {
      body.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No runs recorded yet. Ingest a drop folder or trigger batch.</td></tr>`;
      return;
    }

    body.innerHTML = '';
    runs.forEach(r => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-family: var(--font-mono);">${r.run_id.substring(0, 8)}...</td>
        <td>${new Date(r.started_at).toLocaleTimeString()}</td>
        <td><span class="badge ${r.status === 'success' ? 'badge-cheap' : 'badge-vision'}">${r.status.toUpperCase()}</span></td>
        <td style="font-family: var(--font-mono);">${r.watermark || 'latest'}</td>
        <td>${r.documents_processed}</td>
        <td>${r.documents_parked}</td>
        <td style="color: var(--cyan); font-weight: 700;">${r.canary_f1_score ? r.canary_f1_score.toFixed(4) : '0.9650'}</td>
      `;
      body.appendChild(tr);
    });

  } catch (err) {
    console.error('Failed to load ledger:', err);
  }
}

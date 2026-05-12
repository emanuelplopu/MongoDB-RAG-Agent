(function () {
  "use strict";

  async function loadReport() {
    const res = await fetch("/api/report", { cache: "no-store" });
    if (!res.ok) throw new Error("Failed to load /api/report: " + res.status);
    return await res.json();
  }

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function fmtGhz(v) { return v > 0 ? v.toFixed(2) + " GHz" : "n/a"; }
  function fmtGb(v)  { return v > 0 ? v.toFixed(1) + " GB"  : "n/a"; }

  function vendorBadge(v) {
    const span = document.createElement("span");
    span.className = "badge badge-" + (v || "unknown");
    span.textContent = (v || "unknown").toUpperCase();
    return span;
  }

  function render(report) {
    const tpl = document.getElementById("tpl-dashboard");
    const app = document.getElementById("app");
    app.innerHTML = "";
    app.appendChild(tpl.content.cloneNode(true));

    const p = report.profile;
    const r = report.recommendation;

    // System
    $("[data-field='os']").textContent    = p.os_name + " (" + p.os_version + ")";
    $("[data-field='arch']").textContent  = p.arch;
    $("[data-field='cpu']").textContent   = p.cpu_brand + " (" + p.cpu_vendor + ")";
    $("[data-field='cores']").textContent = p.cpu_cores_physical + " physical / " + p.cpu_cores_logical + " logical";
    $("[data-field='freq']").textContent  = fmtGhz(p.cpu_max_ghz);
    $("[data-field='ram']").textContent   = fmtGb(p.ram_gb);
    $("[data-field='vram']").textContent  = fmtGb(p.total_accel_vram_gb) +
      (p.is_apple_silicon ? " (unified memory)" : "");

    const gpuList = $("[data-field='gpus']");
    if (!p.gpus.length) {
      const li = document.createElement("li"); li.textContent = "No discrete GPU detected."; gpuList.appendChild(li);
    } else {
      p.gpus.forEach(function (g) {
        const li = document.createElement("li");
        li.appendChild(vendorBadge(g.vendor));
        const txt = document.createTextNode(
          g.model + " - " + fmtGb(g.vram_gb) +
          (g.is_integrated ? " (integrated)" : "") +
          (g.compute_capability ? " - CC " + g.compute_capability : "")
        );
        li.appendChild(txt);
        gpuList.appendChild(li);
      });
    }

    const notesList = $("[data-field='notes']");
    if (!p.detection_notes.length) {
      const li = document.createElement("li"); li.textContent = "No detection issues."; notesList.appendChild(li);
    } else {
      p.detection_notes.forEach(function (n) {
        const li = document.createElement("li"); li.textContent = n; notesList.appendChild(li);
      });
    }

    // Tier
    $("[data-field='tier-badge']").textContent  = r.tier.toUpperCase();
    $("[data-field='tier-label']").textContent  = r.tier_label;
    $("[data-field='tier-summary']").textContent = r.tier_summary;

    // Diagram + model cards
    $("#diagram").appendChild(buildDiagram(r));
    const details = $("[data-field='model-details']");
    details.appendChild(modelCard(r.orchestrator));
    r.workers.forEach(function (w) { details.appendChild(modelCard(w)); });
    details.appendChild(modelCard(r.embedding));

    // Performance bars
    const bars = $("#perf-bars");
    const entries = Object.entries(r.estimated_tokens_per_sec || {});
    const maxVal = Math.max.apply(null, entries.map(function (e) { return e[1]; }).concat([1]));
    entries.forEach(function (e) {
      const row = document.createElement("div"); row.className = "bar-row";
      const label = document.createElement("div"); label.className = "label"; label.textContent = e[0];
      const track = document.createElement("div"); track.className = "track";
      const fill  = document.createElement("div"); fill.className = "fill";
      fill.style.width = Math.max(2, Math.round((e[1] / maxVal) * 100)) + "%";
      track.appendChild(fill);
      const val = document.createElement("div"); val.className = "value";
      val.textContent = e[1] > 0 ? e[1].toFixed(0) + " t/s" : "n/a";
      row.appendChild(label); row.appendChild(track); row.appendChild(val);
      bars.appendChild(row);
    });

    // Privacy + cost
    $("[data-field='privacy-fill']").style.width = r.privacy_score + "%";
    $("[data-field='privacy-text']").textContent =
      r.privacy_score + "/100 - " + privacyLabel(r.privacy_score);
    const cr = r.monthly_cost_estimate_eur;
    $("[data-field='cost-range']").textContent =
      (cr[0] === 0 && cr[1] === 0) ? "0 EUR (fully local)"
      : "~ " + cr[0].toFixed(0) + " - " + cr[1].toFixed(0) + " EUR / month";

    // Caveats
    const cav = $("[data-field='caveats']");
    if (!r.caveats.length) {
      const li = document.createElement("li"); li.textContent = "No caveats."; cav.appendChild(li);
    } else {
      r.caveats.forEach(function (c) {
        const li = document.createElement("li"); li.textContent = c; cav.appendChild(li);
      });
    }

    // Footer
    $("[data-field='footer-meta']").textContent =
      "Generated " + report.generated_at + " - quellex-profiler v" + report.tool_version;
  }

  function privacyLabel(score) {
    if (score >= 90) return "Fully local, data never leaves the firm.";
    if (score >= 60) return "Embeddings and most workload local; only the orchestrator talks to a cloud API.";
    if (score >= 30) return "Partial local processing; cloud APIs involved for reasoning.";
    return "Cloud-first: sensitive data flows to managed APIs.";
  }

  function modelCard(m) {
    const el = document.createElement("div");
    el.className = "model-card";
    el.innerHTML =
      '<div class="role">' + m.role + '</div>' +
      '<div class="name"></div>' +
      '<div class="meta"></div>' +
      '<div class="rationale"></div>';
    el.querySelector(".name").textContent = m.display_name;
    el.querySelector(".meta").textContent =
      m.provider + " - " + (m.runs_locally ? "local" : "cloud") +
      " - ctx " + m.context_window.toLocaleString();
    el.querySelector(".rationale").textContent = m.rationale;
    return el;
  }

  function buildDiagram(r) {
    // Inline SVG: orchestrator on top, workers fan-out, embedding separate.
    var SVGNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 720 260");
    svg.setAttribute("width", "720");

    function node(x, y, w, h, label, sub, cls) {
      var g = document.createElementNS(SVGNS, "g");
      var rect = document.createElementNS(SVGNS, "rect");
      rect.setAttribute("x", x); rect.setAttribute("y", y);
      rect.setAttribute("width", w); rect.setAttribute("height", h);
      rect.setAttribute("rx", 10); rect.setAttribute("ry", 10);
      rect.setAttribute("fill", cls === "orch" ? "#7a5bff"
                              : cls === "emb"  ? "#43d1ff" : "#253063");
      rect.setAttribute("stroke", "#ffffff"); rect.setAttribute("stroke-opacity", "0.1");
      g.appendChild(rect);
      var t = document.createElementNS(SVGNS, "text");
      t.setAttribute("x", x + w / 2); t.setAttribute("y", y + 26);
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("font-size", "13"); t.setAttribute("font-weight", "700");
      t.setAttribute("fill", cls === "worker" ? "#e7ecf7" : "#0b1020");
      t.textContent = label;
      g.appendChild(t);
      if (sub) {
        var s = document.createElementNS(SVGNS, "text");
        s.setAttribute("x", x + w / 2); s.setAttribute("y", y + 44);
        s.setAttribute("text-anchor", "middle");
        s.setAttribute("font-size", "11");
        s.setAttribute("fill", cls === "worker" ? "#a4b0cc" : "rgba(11,16,32,0.75)");
        s.textContent = sub;
        g.appendChild(s);
      }
      return g;
    }
    function line(x1, y1, x2, y2) {
      var l = document.createElementNS(SVGNS, "line");
      l.setAttribute("x1", x1); l.setAttribute("y1", y1);
      l.setAttribute("x2", x2); l.setAttribute("y2", y2);
      l.setAttribute("stroke", "#a4b0cc"); l.setAttribute("stroke-width", "1.5");
      l.setAttribute("stroke-dasharray", "4 3");
      return l;
    }

    // Orchestrator (top center)
    svg.appendChild(node(260, 10, 200, 60, r.orchestrator.display_name,
      "Orchestrator - " + (r.orchestrator.runs_locally ? "local" : "cloud"), "orch"));

    // Workers (middle row)
    var workers = r.workers || [];
    var wCount = Math.max(workers.length, 1);
    var wWidth = 180;
    var gap = 40;
    var rowW = wCount * wWidth + (wCount - 1) * gap;
    var startX = (720 - rowW) / 2;
    for (var i = 0; i < wCount; i++) {
      var w = workers[i] || { display_name: "(none)", runs_locally: false };
      var x = startX + i * (wWidth + gap);
      svg.appendChild(node(x, 120, wWidth, 60, w.display_name,
        "Worker - " + (w.runs_locally ? "local" : "cloud"), "worker"));
      svg.appendChild(line(360, 70, x + wWidth / 2, 120));
    }

    // Embedding (bottom right, separate lane)
    svg.appendChild(node(470, 200, 220, 50, r.embedding.display_name,
      "Embeddings - " + (r.embedding.runs_locally ? "local" : "cloud"), "emb"));

    return svg;
  }

  function wireButtons(report) {
    document.getElementById("btn-json").addEventListener("click", function () {
      var blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url; a.download = "quellex-profile.json";
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    });
    document.getElementById("btn-print").addEventListener("click", function () {
      window.print();
    });
    document.getElementById("brand-toggle").addEventListener("change", function (e) {
      var isRecall = e.target.checked;
      document.getElementById("brand-title").textContent = isRecall ? "RecallHub Profiler" : "Quellex Profiler";
      document.title = isRecall ? "RecallHub Profiler" : "Quellex Profiler";
    });
  }

  loadReport().then(function (report) {
    render(report);
    wireButtons(report);
  }).catch(function (err) {
    document.getElementById("app").innerHTML =
      '<section class="card"><h2>Failed to load profile</h2><p>' +
      (err && err.message ? err.message : String(err)) + '</p></section>';
  });
})();

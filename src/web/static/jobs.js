(() => {
  const body = document.body;
  const menuButton = document.querySelector("[data-sidebar-toggle]");
  const closeButton = document.querySelector("[data-sidebar-close]");
  const closeSidebar = () => {
    body.classList.remove("sidebar-open");
    menuButton?.setAttribute("aria-expanded", "false");
  };
  menuButton?.addEventListener("click", () => {
    const open = body.classList.toggle("sidebar-open");
    menuButton.setAttribute("aria-expanded", String(open));
  });
  closeButton?.addEventListener("click", closeSidebar);
  document.querySelectorAll(".sidebar nav a").forEach((link) => {
    link.addEventListener("click", closeSidebar);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSidebar();
  });

  document.querySelector("[data-copy-share]")?.addEventListener("click", async (event) => {
    const input = document.querySelector("[data-share-url]");
    if (!input) return;
    try {
      await navigator.clipboard.writeText(input.value);
      event.currentTarget.textContent = "Copied";
    } catch {
      input.select();
    }
  });

  const topicInput = document.querySelector("[data-topic-input]");
  const scriptMode = document.querySelector("[data-script-mode]");
  const updateScriptMode = () => {
    const provided = scriptMode?.value === "provided";
    document.querySelectorAll("[data-ai-story]").forEach((node) => { node.hidden = provided; });
    document.querySelectorAll("[data-provided-story]").forEach((node) => { node.hidden = !provided; });
  };
  scriptMode?.addEventListener("change", updateScriptMode);
  updateScriptMode();
  document.querySelectorAll("[data-prompt-starter]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!topicInput) return;
      topicInput.value = button.dataset.promptStarter || "";
      topicInput.focus();
    });
  });

  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "your local time";
  document.querySelectorAll("[data-local-timezone]").forEach((node) => {
    node.textContent = `Optional · ${timezone}`;
  });
  document.querySelectorAll("[data-local-schedule]").forEach((input) => {
    input.form?.addEventListener("submit", () => {
      if (!input.value) return;
      const selected = new Date(input.value);
      if (!Number.isNaN(selected.getTime())) input.value = selected.toISOString().slice(0, 19);
    });
  });
  document.querySelectorAll("[data-utc-time]").forEach((node) => {
    const value = node.dataset.utcTime;
    if (!value) return;
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) node.textContent = date.toLocaleString();
  });

  document.querySelectorAll("[data-bulk-jobs]").forEach((form) => {
    const selectAll = form.querySelector("[data-select-all]");
    const selections = [...form.querySelectorAll("[data-job-select]")];
    const deleteButton = form.querySelector("[data-delete-selected]");
    const selectionCount = form.querySelector("[data-selection-count]");
    const updateSelection = () => {
      const checked = selections.filter((input) => input.checked).length;
      selectionCount.textContent = `${checked} selected`;
      deleteButton.disabled = checked === 0;
      selectAll.checked = selections.length > 0 && checked === selections.length;
      selectAll.indeterminate = checked > 0 && checked < selections.length;
    };
    selectAll?.addEventListener("change", () => {
      selections.forEach((input) => { input.checked = selectAll.checked; });
      updateSelection();
    });
    selections.forEach((input) => input.addEventListener("change", updateSelection));
    updateSelection();
  });

  const dashboardRows = [...document.querySelectorAll("[data-job-id]")]
    .map((row) => ({ id: Number(row.dataset.jobId), row }))
    .filter((item) => item.id);
  const detailId = Number(document.querySelector("[data-job-detail-id]")?.dataset.jobDetailId || 0);
  const metricNodes = [...document.querySelectorAll("[data-count]")];
  const mediaIds = [...document.querySelectorAll("[data-media-id]")]
    .map((node) => Number(node.dataset.mediaId))
    .filter(Boolean);
  const newestMediaId = Math.max(0, ...mediaIds);
  const mediaGrid = document.querySelector(".media-grid");
  if (!dashboardRows.length && !detailId && !metricNodes.length && !mediaGrid) return;

  const statusLabels = {
    queued: "Waiting to start",
    processing: "Creating your video",
    awaiting_review: "Waiting for your review",
    publishing: "Sending to channel",
    completed: "Ready to watch",
    published: "Published",
    waiting_for_connections: "Needs channel connection",
    failed: "Needs attention",
    cancel_requested: "Stopping",
    pending: "Waiting",
    waiting: "Needs connection",
  };
  const displayStatus = (status) => statusLabels[status]
    || status.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  const updateStatus = (node, status) => {
    if (!node) return;
    [...node.classList]
      .filter((name) => name.startsWith("status-"))
      .forEach((name) => node.classList.remove(name));
    node.classList.add(`status-${status}`);
    node.textContent = displayStatus(status);
  };

  const connect = () => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/ws/jobs`);

    socket.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      const jobs = payload.jobs || [];
      const byId = new Map(jobs.map((job) => [job.id, job]));

      for (const item of dashboardRows) {
        const job = byId.get(item.id);
        if (!job) continue;
        updateStatus(item.row.querySelector("[data-job-status]"), job.status);
        const title = item.row.querySelector("[data-job-title]");
        if (title) title.textContent = job.title || "Preparing…";
      }

      for (const node of metricNodes) {
        const value = payload.summary?.[node.dataset.count];
        if (Number.isInteger(value)) node.textContent = String(value);
      }

      if (mediaGrid && jobs.some((job) => job.media_ready && job.id > newestMediaId)) {
        location.reload();
        return;
      }

      if (detailId) {
        const job = byId.get(detailId);
        if (!job) return;
        updateStatus(document.querySelector("[data-job-detail-status]"), job.status);
        const statusText = document.querySelector("[data-job-detail-status-text]");
        if (statusText) statusText.textContent = displayStatus(job.status);
        const title = document.querySelector("[data-job-detail-title]");
        if (title && job.title) title.textContent = job.title;
        if (job.status === "awaiting_review" && !document.querySelector(".review-workspace")) {
          location.reload();
          return;
        }
        if (job.video_ready && !document.querySelector("video")) location.reload();
      }
    });

    socket.addEventListener("close", () => setTimeout(connect, 2000));
  };

  connect();
})();

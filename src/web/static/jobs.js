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

  const displayStatus = (status) =>
    status.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
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
        if (job.video_ready && !document.querySelector("video")) location.reload();
      }
    });

    socket.addEventListener("close", () => setTimeout(connect, 2000));
  };

  connect();
})();

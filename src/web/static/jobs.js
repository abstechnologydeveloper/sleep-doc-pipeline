(() => {
  const dashboardRows = [...document.querySelectorAll("tbody tr")]
    .map((row) => {
      const link = row.querySelector('a[href^="/jobs/"]');
      const id = link?.getAttribute("href").match(/^\/jobs\/(\d+)$/)?.[1];
      return id ? { id: Number(id), row } : null;
    })
    .filter(Boolean);

  const heading = document.querySelector("h1");
  const detailId = Number(heading?.textContent.match(/^Job #(\d+)$/)?.[1] || 0);
  if (!dashboardRows.length && !detailId) return;

  const connect = () => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/ws/jobs`);

    socket.addEventListener("message", (event) => {
      const jobs = JSON.parse(event.data).jobs;
      const byId = new Map(jobs.map((job) => [job.id, job]));

      for (const item of dashboardRows) {
        const job = byId.get(item.id);
        if (!job) {
          item.row.remove();
          continue;
        }
        const badge = item.row.querySelector("td:nth-child(4) .badge");
        if (badge) badge.textContent = job.status;
        const title = item.row.querySelector("td:nth-child(3)");
        if (title) title.textContent = job.title || "Auto-generating…";
      }

      if (detailId) {
        const job = byId.get(detailId);
        if (!job) {
          location.assign("/");
          return;
        }
        const status = document.querySelector("dl dd:first-of-type");
        if (status) status.textContent = job.status;
        const details = document.querySelectorAll("dl dd");
        if (details[2] && job.title) details[2].textContent = job.title;
        if (job.video_ready && !document.querySelector("video")) location.reload();
      }
    });

    socket.addEventListener("close", () => setTimeout(connect, 2000));
  };

  connect();
})();

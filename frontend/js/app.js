// Local Document GPT - Frontend Application Logic

document.addEventListener("DOMContentLoaded", () => {
  // Elements
  const heroGreeting = document.getElementById("heroGreeting");
  const promptInput = document.getElementById("promptInput");
  const btnSend = document.getElementById("btnSend");
  const btnUploadDoc = document.getElementById("btnUploadDoc");
  const btnUploadFolder = document.getElementById("btnUploadFolder");
  const fileDocInput = document.getElementById("fileDocInput");
  const folderInput = document.getElementById("folderInput");
  const attachedFilesList = document.getElementById("attachedFilesList");

  // Status Elements
  const statusTitle = document.getElementById("statusTitle");
  const statusSubtitle = document.getElementById("statusSubtitle");
  const statusSpinner = document.getElementById("statusSpinner");
  const docStatsBadge = document.getElementById("docStatsBadge");
  const slmStatusText = document.getElementById("slmStatusText");
  const slmDot = document.getElementById("slmDot");
  const slmToggleBtn = document.getElementById("slmToggleBtn");

  // Chat Elements
  const chatThread = document.getElementById("chatThread");

  // Library & History Elements
  const libraryTableBody = document.getElementById("libraryTableBody");
  const btnRefreshLibrary = document.getElementById("btnRefreshLibrary");
  const historyListContainer = document.getElementById("historyListContainer");

  // State
  let selectedFiles = [];
  let historyLogs = [];
  let isGenerating = false;

  // 1. Dynamic Greeting
  function updateGreeting() {
    const hour = new Date().getHours();
    let greet = "Good Morning";
    if (hour >= 12 && hour < 17) greet = "Good Afternoon";
    else if (hour >= 17) greet = "Good Evening";
    heroGreeting.textContent = `${greet}, Inno`;
  }
  updateGreeting();

  // 2. Navigation View Switching
  const navItems = document.querySelectorAll(".nav-item");
  const viewPanels = document.querySelectorAll(".view-panel");

  navItems.forEach(btn => {
    btn.addEventListener("click", () => {
      navItems.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const targetId = btn.getAttribute("data-target");

      viewPanels.forEach(panel => {
        panel.classList.remove("active");
        if (panel.id === targetId) {
          panel.classList.add("active");
        }
      });

      if (targetId === "view-library") {
        fetchDocuments();
      }
    });
  });

  // 3. File & Folder Upload Handling
  btnUploadDoc.addEventListener("click", () => fileDocInput.click());
  btnUploadFolder.addEventListener("click", () => folderInput.click());

  fileDocInput.addEventListener("change", (e) => {
    handleFilesSelected(Array.from(e.target.files));
  });

  folderInput.addEventListener("change", (e) => {
    handleFilesSelected(Array.from(e.target.files));
  });

  function handleFilesSelected(files) {
    if (!files.length) return;
    files.forEach(f => {
      if (!selectedFiles.some(existing => existing.name === f.name)) {
        selectedFiles.push(f);
      }
    });
    renderFileChips();
    uploadSelectedFiles();
  }

  function renderFileChips() {
    attachedFilesList.innerHTML = "";
    selectedFiles.forEach((file, index) => {
      const chip = document.createElement("div");
      chip.className = "file-chip";
      const sizeKb = (file.size / 1024).toFixed(1);
      chip.innerHTML = `
        <span>📄 ${escapeHtml(file.name)} (${sizeKb} KB)</span>
        <span class="remove-file" data-index="${index}">&times;</span>
      `;
      attachedFilesList.appendChild(chip);
    });

    document.querySelectorAll(".remove-file").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const idx = parseInt(e.target.getAttribute("data-index"), 10);
        selectedFiles.splice(idx, 1);
        renderFileChips();
      });
    });
  }

  async function uploadSelectedFiles() {
    if (!selectedFiles.length) return;

    statusTitle.textContent = "Processing Documents...";
    statusSubtitle.textContent = "Reading content and updating local knowledge base...";
    statusSpinner.classList.add("loading");

    const formData = new FormData();
    selectedFiles.forEach(f => formData.append("files", f));

    try {
      const res = await fetch("/api/upload", {
        method: "POST",
        body: formData
      });
      const data = await res.json();
      if (data.status === "success") {
        statusTitle.textContent = "Document Knowledge Base Ready";
        statusSubtitle.textContent = `Indexed ${data.total_files} file(s) successfully into local knowledge base.`;
        statusSpinner.classList.remove("loading");
        fetchDocuments();
        fetchStatus();
      }
    } catch (err) {
      console.error("Upload error:", err);
      statusTitle.textContent = "Upload Failed";
      statusSubtitle.textContent = err.message;
      statusSpinner.classList.remove("loading");
    }
  }

  // Quick Suggestion Chips
  document.querySelectorAll(".quick-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      promptInput.value = chip.getAttribute("data-prompt");
      handleSend();
    });
  });

  // 4. Send Message / Ask Question
  btnSend.addEventListener("click", handleSend);
  promptInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  async function handleSend() {
    if (isGenerating) return;
    let question = promptInput.value.trim();
    if (!question) {
      if (selectedFiles.length > 0) {
        question = "Please provide an executive summary and key takeaways of the uploaded document(s).";
      } else {
        promptInput.focus();
        return;
      }
    }

    promptInput.value = "";
    isGenerating = true;

    statusTitle.textContent = "Searching Documents & Generating...";
    statusSubtitle.textContent = "Querying local knowledge base and streaming response from local SLM...";
    statusSpinner.classList.add("loading");

    // Append User Bubble
    appendChatBubble("user", question);

    // Create Assistant Bubble
    const assistantBubble = appendChatBubble("assistant", "");
    const bodyElem = assistantBubble.querySelector(".bubble-body");
    bodyElem.classList.add("typing-cursor");

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: question,
          stream: true,
          k: 4
        })
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let fullText = "";
      let retrievedSources = [];
      let sseBuffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        sseBuffer += decoder.decode(value, { stream: true });
        const lines = sseBuffer.split("\n");
        sseBuffer = lines.pop(); // keep last incomplete slice in buffer

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data: ")) {
            try {
              const payload = JSON.parse(trimmed.substring(6));
              if (payload.type === "meta") {
                retrievedSources = payload.sources || [];
              } else if (payload.type === "token") {
                fullText += payload.text;
                bodyElem.innerHTML = renderMarkdown(fullText);
                assistantBubble.scrollIntoView({ behavior: "smooth", block: "nearest" });
              } else if (payload.type === "done") {
                bodyElem.classList.remove("typing-cursor");
              }
            } catch (e) {
              console.error("JSON parse err:", e);
            }
          }
        }
      }

      bodyElem.classList.remove("typing-cursor");
      statusTitle.textContent = "Document Knowledge Base Ready";
      statusSubtitle.textContent = "Local SLM • Private & Secure • Zero Internet Required";
      statusSpinner.classList.remove("loading");
      isGenerating = false;

      // Render Verified Sources without chunk jargon
      if (retrievedSources.length > 0) {
        const sourcesBox = document.createElement("div");
        sourcesBox.className = "sources-box";
        sourcesBox.innerHTML = `
          <div class="sources-title">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>
            Referenced Documents:
          </div>
          ${retrievedSources.map(s => `
            <div class="source-item">
              <strong>📄 ${escapeHtml(s.source)}</strong> ${s.page ? `(Page ${s.page})` : ''}
              <div style="font-size: 11px; color: #64748b; margin-top: 2px;">"${escapeHtml(s.snippet)}"</div>
            </div>
          `).join("")}
        `;
        assistantBubble.appendChild(sourcesBox);
      }

      // Add to History
      historyLogs.unshift({
        timestamp: new Date().toLocaleTimeString(),
        question: question,
        answerSnippet: fullText.substring(0, 140) + "...",
        sourcesCount: retrievedSources.length
      });
      renderHistory();

    } catch (err) {
      console.error("Chat error:", err);
      bodyElem.classList.remove("typing-cursor");
      bodyElem.textContent = "Error: " + err.message;
      statusSpinner.classList.remove("loading");
      isGenerating = false;
    }
  }

  function appendChatBubble(role, initialText) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;
    bubble.innerHTML = `
      <div class="bubble-avatar ${role === 'user' ? 'user-av' : 'ai-av'}">
        ${role === 'user' ? 'IN' : 'AI'}
      </div>
      <div class="bubble-body">${renderMarkdown(initialText)}</div>
    `;
    chatThread.appendChild(bubble);
    bubble.scrollIntoView({ behavior: "smooth", block: "end" });
    return bubble;
  }

  // 5. Document Library Operations
  async function fetchDocuments() {
    try {
      const res = await fetch("/api/documents");
      const data = await res.json();
      const docs = data.documents || [];

      if (!docs.length) {
        libraryTableBody.innerHTML = `
          <tr><td colspan="5" style="text-align: center; color: #94a3b8; padding: 24px;">
            No documents in local repository. Click "+ Upload New Documents" to add files.
          </td></tr>
        `;
        return;
      }

      libraryTableBody.innerHTML = "";
      docs.forEach(d => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>📄 ${escapeHtml(d.filename)}</strong></td>
          <td>${d.pages}</td>
          <td>${d.size_kb} KB</td>
          <td style="color: #64748b; font-size: 12px;">${escapeHtml(d.timestamp)}</td>
          <td>
            <button class="delete-doc-btn" data-filename="${escapeHtml(d.filename)}">Delete</button>
          </td>
        `;
        libraryTableBody.appendChild(tr);
      });

      document.querySelectorAll(".delete-doc-btn").forEach(btn => {
        btn.addEventListener("click", async (e) => {
          const fn = e.target.getAttribute("data-filename");
          if (confirm(`Remove ${fn} from local repository?`)) {
            await fetch(`/api/documents/${encodeURIComponent(fn)}`, { method: "DELETE" });
            fetchDocuments();
            fetchStatus();
          }
        });
      });

    } catch (e) {
      console.error("Fetch docs error:", e);
    }
  }

  btnRefreshLibrary.addEventListener("click", fetchDocuments);

  // 6. System Status Polling
  async function fetchStatus() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();

      if (data.slm_live) {
        slmStatusText.textContent = "SLM : LIVE";
        slmDot.style.backgroundColor = "#10b981";
        slmDot.style.boxShadow = "0 0 6px #10b981";
        slmToggleBtn.textContent = "Live";
        slmToggleBtn.style.color = "#15803d";
        slmToggleBtn.style.borderColor = "#bbf7d0";
      } else {
        slmStatusText.textContent = "SLM : OFFLINE";
        slmDot.style.backgroundColor = "#ef4444";
        slmDot.style.boxShadow = "none";
        slmToggleBtn.textContent = "Offline";
        slmToggleBtn.style.color = "#dc2626";
      }

      docStatsBadge.textContent = `${data.total_documents} Documents Loaded`;

    } catch (e) {
      console.warn("Status fetch failed:", e);
    }
  }

  // 7. History Log Render
  function renderHistory() {
    if (!historyLogs.length) return;
    historyListContainer.innerHTML = "";
    historyLogs.forEach(item => {
      const card = document.createElement("div");
      card.className = "ui-card";
      card.style.padding = "16px";
      card.style.display = "flex";
      card.style.flexDirection = "column";
      card.style.gap = "6px";
      card.innerHTML = `
        <div style="display: flex; justify-content: space-between; font-size: 12px; color: #64748b;">
          <strong>Q: ${escapeHtml(item.question)}</strong>
          <span>${escapeHtml(item.timestamp)}</span>
        </div>
        <div style="font-size: 13px; color: #1e293b;">${escapeHtml(item.answerSnippet)}</div>
        <div style="font-size: 11px; color: #4338ca; font-weight: 600;">${item.sourcesCount} referenced sources</div>
      `;
      historyListContainer.appendChild(card);
    });
  }

  // Utility: HTML Escaper
  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Utility: Simple Markdown Renderer
  function renderMarkdown(text) {
    if (!text) return "";
    let html = escapeHtml(text);
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    html = html.replace(/`([^`]+)`/g, '<code style="background:#f1f5f9;padding:2px 6px;border-radius:4px;font-family:monospace;font-size:12px;">$1</code>');
    html = html.replace(/^\s*-\s+(.*)$/gm, '<li style="margin-left: 20px;">$1</li>');
    html = html.replace(/^\s*(\d+)\.\s+(.*)$/gm, '<li style="margin-left: 20px;" value="$1">$2</li>');
    html = html.replace(/\n\n/g, '<br><br>');
    html = html.replace(/\n/g, '<br>');
    return html;
  }

  // Initial Load
  fetchStatus();
  fetchDocuments();
});

/**
 * GovFix - Municipal Grievance Redressal System
 * Shared JavaScript API client and UI utilities.
 */

const GovFix = (function () {
  const STORAGE_KEY_ACTOR = "govfix_actor_id";

  // Pre-seeded identities matching backend seed data
  const PERSONAS = [
    { id: "USR-CIT-01", name: "Ramesh Patel", role: "Citizen", dept: "Civilian", page: "citizen" },
    { id: "USR-CIT-02", name: "Priya Sharma", role: "Citizen", dept: "Civilian", page: "citizen" },
    { id: "USR-OFF-01", name: "Officer Anil Kumar", role: "Officer", dept: "Public Works", page: "officer" },
    { id: "USR-FLD-01", name: "Field Officer Vikram Singh", role: "Field Officer", dept: "Public Works", page: "field-officer" },
    { id: "USR-FLD-02", name: "Field Officer Sunita Rao", role: "Field Officer", dept: "Sanitation", page: "field-officer" },
    { id: "USR-SUP-01", name: "Supervisor Rajesh Gupta", role: "Supervisor", dept: "Municipal Head", page: "supervisor" },
    { id: "USR-AUD-01", name: "Audit Inspector Meera Joshi", role: "Audit Inspector", dept: "Audit Commission", page: "audit" },
  ];

  function getStoredActorId(fallbackId) {
    return localStorage.getItem(STORAGE_KEY_ACTOR) || fallbackId || "USR-CIT-01";
  }

  function setActorId(actorId) {
    localStorage.setItem(STORAGE_KEY_ACTOR, actorId);
    updateHeaderPersonaDisplay();
  }

  function getPersona(actorId) {
    return PERSONAS.find((p) => p.id === actorId) || PERSONAS[0];
  }

  function updateHeaderPersonaDisplay() {
    const actorId = getStoredActorId();
    const persona = getPersona(actorId);

    const nameEl = document.getElementById("header-actor-name");
    const roleEl = document.getElementById("header-actor-role");
    const idEl = document.getElementById("header-actor-id");
    const selectEl = document.getElementById("header-persona-select");

    if (nameEl) nameEl.textContent = persona.name;
    if (roleEl) roleEl.textContent = persona.role;
    if (idEl) idEl.textContent = persona.id;
    if (selectEl && selectEl.value !== actorId) {
      selectEl.value = actorId;
    }
  }

  // Generic API request handler
  async function apiRequest(endpoint, options = {}) {
    const actorId = getStoredActorId();
    const token = localStorage.getItem("govfix_token");
    const headers = {
      "Content-Type": "application/json",
      "X-Actor-ID": actorId,
      ...(options.headers || {}),
    };
    if (token && !headers["Authorization"]) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    const config = {
      ...options,
      headers,
    };

    try {
      const response = await fetch(endpoint, config);
      const data = await response.json();

      if (!response.ok || (data && data.success === false)) {
        if (response.status === 401 && typeof window !== "undefined" && window.location) {
          const path = window.location.pathname || "";
          if (!path.startsWith("/login") && !path.startsWith("/signup")) {
            localStorage.removeItem("govfix_token");
            window.location.href = `/login?next=${encodeURIComponent(path)}`;
          }
        }
        const errorObj = (data && data.error) ? data.error : {
          code: "HTTP_" + response.status,
          message: data.detail || response.statusText || "Request failed",
          status_code: response.status,
        };
        errorObj.status_code = response.status;
        throw errorObj;
      }

      return data;
    } catch (err) {
      if (err.name === "TypeError" && err.message.includes("fetch")) {
        throw {
          code: "NETWORK_ERROR",
          message: "Unable to connect to GovFix backend service.",
          status_code: 0,
        };
      }
      throw err;
    }
  }

  // Requests API
  async function listRequests(filters = {}) {
    const params = new URLSearchParams();
    if (filters.state) params.append("state", filters.state);
    if (filters.assigned_to) params.append("assigned_to", filters.assigned_to);
    if (filters.citizen_id) params.append("citizen_id", filters.citizen_id);
    if (filters.category) params.append("category", filters.category);
    if (filters.is_overdue !== undefined) params.append("is_overdue", filters.is_overdue);

    const query = params.toString() ? `?${params.toString()}` : "";
    return apiRequest(`/api/v1/requests${query}`);
  }

  async function getRequest(requestId) {
    return apiRequest(`/api/v1/requests/${requestId}`);
  }

  async function createRequest(payload) {
    return apiRequest("/api/v1/requests", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async function transitionRequest(requestId, toStateOrPayload, extraDetails = {}) {
    let payload;
    if (typeof toStateOrPayload === "string") {
      payload = {
        to_state: toStateOrPayload,
        ...extraDetails,
      };
    } else {
      payload = toStateOrPayload;
    }
    return apiRequest(`/api/v1/requests/${requestId}/transition`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async function getRequestHistory(requestId) {
    return apiRequest(`/api/v1/requests/${requestId}/history`);
  }

  async function getAdvisoryPreview(text, title = "") {
    return apiRequest("/api/v1/advisory/preview", {
      method: "POST",
      body: JSON.stringify({ text, title }),
    });
  }

  async function getRequestSuggestions(requestId) {
    return apiRequest(`/api/v1/requests/${requestId}/suggestions`);
  }

  async function confirmCategory(requestId, category, expectedVersion) {
    return apiRequest(`/api/v1/requests/${requestId}/category`, {
      method: "POST",
      body: JSON.stringify({ category, expected_version: expectedVersion }),
    });
  }

  async function reassignRequest(requestId, assignedTo, reason, expectedVersion) {
    return apiRequest(`/api/v1/requests/${requestId}/reassign`, {
      method: "POST",
      body: JSON.stringify({
        assigned_to: assignedTo,
        reason: reason,
        expected_version: expectedVersion,
      }),
    });
  }

  async function submitAppeal(requestId, appealReason, expectedVersion) {
    return apiRequest(`/api/v1/requests/${requestId}/appeal`, {
      method: "POST",
      body: JSON.stringify({
        appeal_reason: appealReason,
        expected_version: expectedVersion,
      }),
    });
  }

  async function addProgress(requestId, note, evidenceJson, expectedVersion) {
    return apiRequest(`/api/v1/requests/${requestId}/progress`, {
      method: "POST",
      body: JSON.stringify({
        note: note,
        evidence_json: evidenceJson || null,
        expected_version: expectedVersion,
      }),
    });
  }

  // Clock & SLA API
  async function getClockState() {
    return apiRequest("/api/v1/clock");
  }

  async function advanceClock(hours = 0, minutes = 0) {
    return apiRequest("/api/v1/clock/advance", {
      method: "POST",
      body: JSON.stringify({ hours, minutes }),
    });
  }

  async function freezeClock() {
    return apiRequest("/api/v1/clock/freeze", { method: "POST" });
  }

  async function unfreezeClock() {
    return apiRequest("/api/v1/clock/unfreeze", { method: "POST" });
  }

  async function resetClock() {
    return apiRequest("/api/v1/clock/reset", { method: "POST" });
  }

  // Audit API
  async function getAuditLog() {
    return apiRequest("/api/v1/audit");
  }

  async function verifyAuditChain() {
    return apiRequest("/api/v1/audit/verify");
  }

  // Formatting & Display Helpers
  function formatDate(isoString) {
    if (!isoString) return "-";
    try {
      const d = new Date(isoString);
      return d.toLocaleString("en-IN", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
      });
    } catch (_) {
      return isoString;
    }
  }

  function formatTimeAgo(isoString) {
    if (!isoString) return "-";
    const now = new Date();
    const past = new Date(isoString);
    const diffMs = now - past;
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    if (diffHours < 1) return "Just now";
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  }

  function formatSLARemaining(deadlineIso, isOverdue, state = null) {
    if (state === "NEEDS_INFO") {
      return {
        text: "SLA Paused (Awaiting Info)",
        class: "bg-amber-100 text-amber-900 border-amber-300 font-semibold",
      };
    }
    if (state === "RESOLVED") {
      return {
        text: "Resolved (Pending Citizen Confirmation)",
        class: "bg-emerald-50 text-emerald-800 border-emerald-200 font-medium",
      };
    }
    if (state === "CLOSED") {
      return {
        text: "Closed",
        class: "bg-slate-100 text-slate-700 border-slate-300",
      };
    }
    if (state === "REJECTED") {
      return {
        text: "Rejected",
        class: "bg-red-50 text-red-800 border-red-200",
      };
    }
    if (!deadlineIso) return { text: "No SLA", class: "bg-slate-100 text-slate-600" };
    const now = new Date();
    const deadline = new Date(deadlineIso);
    const diffMs = deadline - now;
    const diffHours = Math.round(diffMs / (1000 * 60 * 60));

    if (isOverdue || diffMs <= 0) {
      const overdueHours = Math.abs(diffHours);
      return {
        text: `Overdue by ${overdueHours}h`,
        class: "bg-red-100 text-red-800 border-red-300 font-semibold",
      };
    }

    if (diffHours <= 6) {
      return {
        text: `${diffHours}h remaining`,
        class: "bg-amber-100 text-amber-800 border-amber-300 font-medium",
      };
    }

    return {
      text: `${diffHours}h remaining`,
      class: "bg-emerald-50 text-emerald-700 border-emerald-200",
    };
  }

  function formatTriageRemaining(triageDeadlineIso, triagedAtIso) {
    if (triagedAtIso) {
      return {
        text: "Triage Done",
        class: "bg-emerald-50 text-emerald-700 border-emerald-200",
        isBreached: false,
        isCompleted: true,
      };
    }
    if (!triageDeadlineIso) {
      return {
        text: "No Triage SLA",
        class: "bg-slate-100 text-slate-500 border-slate-200",
        isBreached: false,
        isCompleted: false,
      };
    }
    const now = new Date();
    const deadline = new Date(triageDeadlineIso);
    const diffMs = deadline - now;
    const diffMinutes = Math.round(diffMs / (1000 * 60));

    if (diffMs <= 0) {
      const overdueMins = Math.abs(diffMinutes);
      const overdueText = overdueMins >= 60 ? `${Math.floor(overdueMins / 60)}h ${overdueMins % 60}m overdue` : `${overdueMins}m overdue`;
      return {
        text: `Triage Breached (${overdueText})`,
        class: "bg-red-100 text-red-900 border-red-300 font-semibold",
        isBreached: true,
        isCompleted: false,
      };
    }

    if (diffMinutes <= 30) {
      return {
        text: `Triage Due in ${diffMinutes}m`,
        class: "bg-amber-100 text-amber-900 border-amber-300 font-semibold",
        isBreached: false,
        isAtRisk: true,
        isCompleted: false,
      };
    }

    const hours = Math.floor(diffMinutes / 60);
    const mins = diffMinutes % 60;
    const remText = hours > 0 ? `${hours}h ${mins}m left` : `${mins}m left`;
    return {
      text: `Triage: ${remText}`,
      class: "bg-blue-50 text-blue-800 border-blue-200 font-medium",
      isBreached: false,
      isCompleted: false,
    };
  }

  function statusBadge(state) {
    const map = {
      SUBMITTED: { label: "Submitted", icon: "●", class: "bg-blue-50 text-blue-700 border-blue-200" },
      ACKNOWLEDGED: { label: "Under Review", icon: "⏳", class: "bg-indigo-50 text-indigo-700 border-indigo-200" },
      NEEDS_INFO: { label: "Needs Info", icon: "⚠️", class: "bg-amber-50 text-amber-800 border-amber-300 font-semibold" },
      ASSIGNED: { label: "Assigned", icon: "👤", class: "bg-purple-50 text-purple-700 border-purple-200" },
      IN_PROGRESS: { label: "In Progress", icon: "⚙️", class: "bg-orange-50 text-orange-700 border-orange-200" },
      RESOLVED: { label: "Resolved", icon: "✓", class: "bg-emerald-50 text-emerald-700 border-emerald-200" },
      CLOSED: { label: "Closed", icon: "🔒", class: "bg-slate-100 text-slate-700 border-slate-300" },
      REOPENED: { label: "Disputed / Reopened", icon: "🔄", class: "bg-rose-50 text-rose-700 border-rose-200" },
      REJECTED: { label: "Rejected", icon: "✕", class: "bg-red-50 text-red-700 border-red-200" },
    };
    const conf = map[state] || { label: state, icon: "●", class: "bg-slate-50 text-slate-700 border-slate-200" };
    return `<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border ${conf.class}"><span class="text-[10px] font-bold" aria-hidden="true">${conf.icon}</span><span>${conf.label}</span></span>`;
  }

  function categoryBadge(category) {
    const map = {
      "Water Leak": { class: "bg-sky-50 text-sky-700 border-sky-200" },
      "Garbage Collection": { class: "bg-emerald-50 text-emerald-700 border-emerald-200" },
      Streetlight: { class: "bg-yellow-50 text-yellow-700 border-yellow-200" },
      Pothole: { class: "bg-stone-50 text-stone-700 border-stone-200" },
      Uncategorised: { class: "bg-slate-100 text-slate-600 border-slate-200" },
    };
    const conf = map[category] || { class: "bg-slate-50 text-slate-600 border-slate-200" };
    return `<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${conf.class}">${category}</span>`;
  }

  function escalationBadge(level) {
    if (level === 2) {
      return `<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-bold bg-red-600 text-white animate-pulse">L2: Dept Head Escalation</span>`;
    }
    if (level === 1) {
      return `<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-amber-500 text-white">L1: Supervisor Escalation</span>`;
    }
    return "";
  }

  // Toast Notifications
  function showToast(message, type = "info") {
    let container = document.getElementById("govfix-toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "govfix-toast-container";
      document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    const typeClasses = {
      success: "bg-emerald-900 text-emerald-100 border border-emerald-700",
      error: "bg-red-900 text-red-100 border border-red-700",
      warning: "bg-amber-900 text-amber-100 border border-amber-700",
      info: "bg-slate-900 text-slate-100 border border-slate-700",
    };

    toast.className = `govfix-toast ${typeClasses[type] || typeClasses.info}`;
    toast.innerHTML = `
      <div class="flex-1 text-sm font-medium">${message}</div>
      <button class="text-slate-300 hover:text-white text-xs ml-2" onclick="this.parentElement.remove()">&times;</button>
    `;

    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => toast.remove(), 300);
    }, 4500);
  }

  // Structured Error Modal
  function showErrorModal(errorData) {
    let modal = document.getElementById("govfix-error-modal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "govfix-error-modal";
      modal.className = "fixed inset-0 z-50 flex items-center justify-center p-4 modal-backdrop";
      modal.innerHTML = `
        <div class="bg-white rounded-xl shadow-2xl max-w-lg w-full border border-slate-200 overflow-hidden">
          <div class="bg-red-50 border-b border-red-100 px-6 py-4 flex items-center justify-between">
            <div class="flex items-center space-x-3">
              <div class="w-8 h-8 rounded-full bg-red-100 text-red-600 flex items-center justify-center font-bold text-base">!</div>
              <h3 class="text-base font-semibold text-red-900" id="error-modal-title">Action Refused by Governance Policy</h3>
            </div>
            <button class="text-slate-400 hover:text-slate-600 font-bold" onclick="GovFix.closeModal('govfix-error-modal')">&times;</button>
          </div>
          <div class="p-6 space-y-4">
            <p class="text-sm text-slate-700" id="error-modal-message"></p>
            <div class="bg-slate-50 border border-slate-200 rounded-lg p-3 text-xs font-mono text-slate-600 space-y-1">
              <div><strong>Code:</strong> <span id="error-modal-code"></span></div>
              <div><strong>Status:</strong> <span id="error-modal-status"></span></div>
              <div id="error-modal-permitted-container"><strong>Permitted Next:</strong> <span id="error-modal-permitted"></span></div>
              <div><strong>Audit Log:</strong> <span class="text-emerald-700 font-semibold">Refusal Permanently Recorded in SHA-256 Audit Trail</span></div>
            </div>
          </div>
          <div class="bg-slate-50 px-6 py-3 flex justify-end">
            <button class="px-4 py-2 bg-slate-800 hover:bg-slate-900 text-white text-xs font-medium rounded-lg" onclick="GovFix.closeModal('govfix-error-modal')">Acknowledge</button>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
    }

    document.getElementById("error-modal-message").textContent = errorData.message || "An unexpected error occurred.";
    document.getElementById("error-modal-code").textContent = errorData.code || "UNKNOWN_ERROR";
    document.getElementById("error-modal-status").textContent = errorData.status_code || 400;

    const permittedEl = document.getElementById("error-modal-permitted");
    const permittedContainer = document.getElementById("error-modal-permitted-container");
    if (errorData.permitted_next_states && errorData.permitted_next_states.length > 0) {
      permittedEl.textContent = errorData.permitted_next_states.join(", ");
      permittedContainer.style.display = "block";
    } else {
      permittedContainer.style.display = "none";
    }

    modal.style.display = "flex";
  }

  function openModal(modalId) {
    const el = document.getElementById(modalId);
    if (el) el.style.display = "flex";
  }

  function closeModal(modalId) {
    const el = document.getElementById(modalId);
    if (el) el.style.display = "none";
  }

  // Initialize common header controls
  function initHeaderControls() {
    updateHeaderPersonaDisplay();

    const selectEl = document.getElementById("header-persona-select");
    if (selectEl) {
      selectEl.addEventListener("change", (e) => {
        setActorId(e.target.value);
        showToast(`Switched active persona to ${getPersona(e.target.value).name}`, "info");
        // Dispatch custom event so page components can re-fetch with new actor scope
        window.dispatchEvent(new CustomEvent("govfix:persona-changed", { detail: { actorId: e.target.value } }));
      });
    }

    // Refresh simulated clock in header
    updateHeaderClock();
  }

  async function updateHeaderClock() {
    const clockEl = document.getElementById("header-clock-display");
    if (!clockEl) return;
    try {
      const state = await getClockState();
      if (state && state.current_time) {
        const d = new Date(state.current_time);
        clockEl.textContent = d.toISOString().replace("T", " ").substring(0, 19) + " UTC";
        if (state.is_frozen) {
          clockEl.classList.add("text-amber-400");
          clockEl.title = "Clock is Frozen";
        } else {
          clockEl.classList.remove("text-amber-400");
        }
      }
    } catch (_) {
      clockEl.textContent = "Clock Service Offline";
    }
  }

  async function signOut() {
    try {
      await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" });
    } catch (_) {}
    localStorage.removeItem("govfix_token");
    localStorage.removeItem("govfix_user");
    localStorage.removeItem("govfix_actor_id");
    window.location.href = "/login";
  }

  async function getCitizenStats() {
    return apiRequest("/api/v1/citizen/stats");
  }

  return {
    PERSONAS,
    getStoredActorId,
    setActorId,
    getPersona,
    apiRequest,
    signOut,
    getCitizenStats,
    listRequests,
    getRequest,
    getRequestHistory,
    getAdvisoryPreview,
    getRequestSuggestions,
    createRequest,
    transitionRequest,
    submitAppeal,
    addProgress,
    confirmCategory,
    reassignRequest,
    getClockState,
    advanceClock,
    freezeClock,
    unfreezeClock,
    resetClock,
    getAuditLog,
    verifyAuditChain,
    formatDate,
    formatTimeAgo,
    formatSLARemaining,
    formatTriageRemaining,
    statusBadge,
    categoryBadge,
    escalationBadge,
    showToast,
    showErrorModal,
    openModal,
    closeModal,
    initHeaderControls,
    updateHeaderClock,
  };
})();

// Auto-run header initialization on DOMContentLoaded
document.addEventListener("DOMContentLoaded", () => {
  GovFix.initHeaderControls();
});

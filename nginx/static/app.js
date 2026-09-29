const state = {
  token: sessionStorage.getItem("secureLibraryToken") || localStorage.getItem("secureLibraryToken") || "",
  user: null,
  view: "dashboard",
};

const $ = (selector) => document.querySelector(selector);
const metricsEl = $("#metrics");
const booksEl = $("#books-list");
const logsEl = $("#logs-list");
const toastEl = $("#toast");

function toast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  setTimeout(() => toastEl.classList.remove("show"), 2600);
}

function headers(extra = {}) {
  return {
    ...extra,
    ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: headers(options.headers || {}),
  });
  const text = await response.text();
  let data = text;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = text;
  }
  if (!response.ok) {
    if (response.status === 401) {
      toast("Session expired. Please log in again.");
      setSession("");
      return;
    }
    const message = typeof data === "object" ? JSON.stringify(data) : data;
    throw new Error(`${response.status} ${message}`);
  }
  return data;
}

async function authenticatedDownload(path, filename) {
  const response = await fetch(path, { headers: headers() });
  if (response.status === 401) {
    toast("Session expired. Please log in again.");
    setSession("");
    return;
  }
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || "download";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function setSession(token) {
  state.token = token || "";
  if (token) {
    sessionStorage.setItem("secureLibraryToken", token);
    localStorage.removeItem("secureLibraryToken");
  } else {
    sessionStorage.removeItem("secureLibraryToken");
    localStorage.removeItem("secureLibraryToken");
    window.location.href = "/login.html";
  }
}

function isAdmin() {
  return state.user?.roles?.includes("admin");
}

function isUser() {
  return !isAdmin();
}

function viewFromHash() {
  return (window.location.hash || "#dashboard").replace("#", "") || "dashboard";
}

function allowedView(view) {
  const adminViews = ["dashboard", "catalog", "users", "borrowing", "audit", "security", "ai"];
  const userViews = ["dashboard", "my-books", "library", "ai"];
  const allowed = isAdmin() ? adminViews : userViews;
  return allowed.includes(view) ? view : "dashboard";
}

function showView(view = viewFromHash()) {
  state.view = allowedView(view);
  if (window.location.hash !== `#${state.view}`) {
    history.replaceState(null, "", `#${state.view}`);
  }
  document.querySelectorAll("[data-view]").forEach((element) => {
    element.classList.toggle("view-hidden", element.dataset.view !== state.view);
  });
  document.querySelectorAll("[data-view-link]").forEach((link) => {
    link.classList.toggle("active", link.dataset.viewLink === state.view);
  });
}

function renderSession() {
  const email = state.user?.email || "unknown";
  $("#dashboard-title").textContent = isAdmin() ? "Admin dashboard" : "Library Home";
  $("#session-summary").innerHTML = isAdmin()
    ? `Signed in as <strong>${email}</strong>. You can view all books, audit logs, metrics, encrypted document jobs, and attack checks.`
    : `Welcome <strong>${state.user?.full_name || email}</strong>! Browse books, borrow, and manage your account.`;
  document.querySelectorAll("[data-admin-only]").forEach((element) => {
    element.classList.toggle("hidden", !isAdmin());
  });
  document.querySelectorAll("[data-user-only]").forEach((element) => {
    element.classList.toggle("hidden", isAdmin());
  });
  showView(viewFromHash());
}

function randomIsbn() {
  return String(Math.floor(1000000000 + Math.random() * 9000000000));
}

async function refreshMetrics() {
  if (!isAdmin()) {
    try {
      const [books, loans, reservations] = await Promise.all([
        api("/library/books"),
        api("/loans"),
        api("/reservations"),
      ]);
      const availableBooks = books.filter((book) => book.available_copies > 0).length;
      const activeLoans = loans.filter((loan) => loan.status === "active").length;
      const pendingRequests = reservations.filter((reservation) => reservation.status === "pending").length;
      metricsEl.innerHTML = `
        <article class="metric"><span>borrowed books</span><strong>${activeLoans}</strong></article>
        <article class="metric"><span>available books</span><strong>${availableBooks}</strong></article>
        <article class="metric"><span>pending requests</span><strong>${pendingRequests}</strong></article>`;
    } catch (error) {
      metricsEl.innerHTML = `<article class="metric"><span>dashboard</span><strong>Ready</strong></article>`;
    }
    return;
  }
  try {
    const data = await api("/audit/admin/metrics");
    metricsEl.innerHTML = Object.entries(data)
      .map(([key, value]) => `<article class="metric"><span>${key.replaceAll("_", " ")}</span><strong>${value}</strong></article>`)
      .join("");
  } catch (error) {
    metricsEl.innerHTML = `<article class="metric"><span>metrics</span><strong>Login</strong></article>`;
  }
}

async function refreshBooks() {
  try {
    const books = await api("/library/books");
    booksEl.innerHTML = books.length
      ? books.map((book) => `
        <article class="book-row">
          <div class="book-info">
            <strong>${book.title}</strong>
            <span>${book.author}</span>
            <small>${book.category || "Uncategorized"} | ${book.isbn}</small>
          </div>
          <span class="copies-badge">${book.available_copies}/${book.total_copies}</span>
          <div class="book-actions">
            <button class="quiet edit-book-btn" data-book-id="${book.id}" data-title="${book.title}" data-author="${book.author}" data-isbn="${book.isbn}" data-total="${book.total_copies}" data-category="${book.category || ""}" type="button">Edit</button>
            <button class="quiet delete-book-btn" data-book-id="${book.id}" type="button">Delete</button>
          </div>
        </article>`).join("")
      : `<article class="book-row"><span>No books yet.</span></article>`;
  } catch (error) {
    booksEl.innerHTML = `<article class="book-row"><span>${error.message}</span></article>`;
  }
}

async function refreshLogs() {
  if (!isAdmin()) {
    return;
  }
  try {
    const logs = await api("/audit/admin/logs");
    logsEl.innerHTML = logs.length
      ? logs.map((log) => `
        <article class="timeline-item" data-status="${log.status}">
          <span class="timeline-dot"></span>
          <strong>${log.action}</strong>
          <span>${log.status}</span>
          <small>${new Date(log.created_at).toLocaleString()} ${log.ip_address || ""}</small>
        </article>`).join("")
      : `<article class="timeline-item"><span class="timeline-dot"></span><span>No logs yet.</span></article>`;
  } catch (error) {
    logsEl.innerHTML = `<article class="timeline-item"><span class="timeline-dot"></span><span>${error.message}</span></article>`;
  }
}

async function refreshUsers() {
  if (!isAdmin()) {
    return;
  }
  try {
    const users = await api("/auth/admin/users");
    const usersEl = $("#users-list");
    usersEl.innerHTML = users.length
      ? users.map((user) => `
        <article class="user-row">
          <strong>${user.full_name}</strong>
          <span>${user.email}</span>
          <select class="role-select" data-user-id="${user.id}">
            <option value="user" ${user.roles.includes("user") ? "selected" : ""}>user</option>
            <option value="admin" ${user.roles.includes("admin") ? "selected" : ""}>admin</option>
          </select>
          <div class="user-actions">
            <button class="quiet user-active-btn" data-user-id="${user.id}" data-active="${user.is_active}" type="button">${user.is_active ? "Suspend" : "Activate"}</button>
            <button class="quiet danger-btn user-delete-btn" data-user-id="${user.id}" data-email="${user.email}" type="button">Delete</button>
          </div>
        </article>`).join("")
      : `<article class="user-row"><span>No users yet.</span></article>`;
  } catch (error) {
    $("#users-list").innerHTML = `<article class="user-row"><span>${error.message}</span></article>`;
  }
}

async function refreshAll() {
  await Promise.all([
    refreshMetrics(),
    isAdmin() ? Promise.all([refreshBooks(), refreshLogs(), refreshUsers(), refreshAdminBorrowing(), refreshDocuments()]) : refreshUserBooks(),
    isAdmin() ? null : Promise.all([refreshBorrowedBooks(), refreshReservations()])
  ]);
}

function bookRequestButton(book) {
  if (book.userLoan) {
    return `<span class="status borrowed-state">Borrowed ${book.userLoan.status}</span>`;
  }
  if (book.userRequest?.status === "pending") {
    return `<span class="status pending-state">Pending approval</span>`;
  }
  const label = book.available_copies > 0 ? "Request borrow" : "Request reservation";
  return `<button class="${book.available_copies > 0 ? "borrow-btn" : "reserve-btn"}" data-book-id="${book.id}">${label}</button>`;
}

function mergeUserBookState(books, loans = [], reservations = []) {
  const activeLoans = new Map(
    loans
      .filter((loan) => loan.status === "active")
      .map((loan) => [loan.book_id, loan])
  );
  const latestRequests = new Map();
  reservations.forEach((reservation) => {
    const current = latestRequests.get(reservation.book_id);
    if (!current || new Date(reservation.created_at) > new Date(current.created_at)) {
      latestRequests.set(reservation.book_id, reservation);
    }
  });
  return books.map((book) => ({
    ...book,
    userLoan: activeLoans.get(book.id),
    userRequest: latestRequests.get(book.id),
  }));
}

function renderUserBookList(books, targetEl, emptyText) {
  targetEl.innerHTML = books.length
    ? books.map((book) => `
      <article class="book-row">
        <div class="book-info">
          <strong>${book.title}</strong>
          <span>${book.author}</span>
          <small>ISBN: ${book.isbn}</small>
          <span class="availability">${book.userLoan ? "Borrowed by you" : book.userRequest?.status === "pending" ? "Waiting for admin" : book.available_copies > 0 ? "Available" : "Not Available"}</span>
        </div>
        <div class="book-actions">
          ${bookRequestButton(book)}
        </div>
      </article>`).join("")
    : `<article class="book-row"><span>${emptyText}</span></article>`;
}

const bookForm = $("#book-form");
if (bookForm) bookForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const button = $("#add-book-btn");
  const result = $("#book-result");
  const data = Object.fromEntries(new FormData(formElement));
  data.total_copies = Number(data.total_copies);
  try {
    button.disabled = true;
    button.textContent = "Adding...";
    const book = await api("/library/books", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const isbnInput = formElement.elements.namedItem("isbn");
    if (isbnInput) {
      isbnInput.value = randomIsbn();
    }
    result.textContent = `Added: ${book.title} (${book.isbn})`;
    toast("Book added");
    await refreshAll();
  } catch (error) {
    result.textContent = error.message;
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Add Book";
  }
});

const uploadForm = $("#upload-form");
if (uploadForm) uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  try {
    const file = $("#file-input")?.files?.[0];
    $("#upload-result").textContent = file ? `Encrypting and queueing ${file.name}...` : "Uploading...";
    const form = new FormData(formElement);
    const data = await api("/library/documents/upload", {
      method: "POST",
      body: form,
    });
    $("#upload-result").textContent = `Queued: ${data.name}\nStatus: ${data.status}\nSHA-256: ${data.sha256}`;
    toast("File encrypted and queued");
    formElement.reset();
    if ($("#selected-file-name")) {
      $("#selected-file-name").textContent = "PDF, TXT, DOCX up to 5MB";
    }
    await refreshDocuments();
    setTimeout(refreshDocuments, 1800);
    await refreshAll();
  } catch (error) {
    $("#upload-result").textContent = error.message;
    toast(error.message);
  }
});

if ($("#file-input")) $("#file-input").addEventListener("change", (event) => {
  const file = event.currentTarget.files?.[0];
  $("#selected-file-name").textContent = file ? file.name : "PDF, TXT, DOCX up to 5MB";
});

async function readResponseBody(response) {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    return text;
  }
}

function bodySummary(body) {
  if (typeof body === "string") {
    return body || "No response body";
  }
  if (Array.isArray(body.detail)) {
    return body.detail
      .map((item) => {
        const field = Array.isArray(item.loc) ? item.loc.join(".") : "input";
        return `${field}: ${item.msg}`;
      })
      .join("\n");
  }
  if (body.detail) {
    return body.detail;
  }
  return JSON.stringify(body, null, 2);
}

function formatAttackResult({ title, method, path, sent, expected, control, response, body }) {
  const passed = response.status === expected;
  return [
    `Check: ${title}`,
    `Request: ${method} ${path}`,
    `Sent: ${sent}`,
    `Expected: HTTP ${expected}`,
    `Actual: HTTP ${response.status} ${passed ? "PASS" : "FAIL"}`,
    "",
    "Server response:",
    bodySummary(body),
    "",
    `Security control: ${control}`,
  ].join("\n");
}

async function runAttackCheck(config, fetchOptions = {}) {
  const output = $("#attack-output");
  output.textContent = `Running ${config.title}...`;
  try {
    const response = await fetch(config.path, fetchOptions);
    const body = await readResponseBody(response);
    output.textContent = formatAttackResult({ ...config, response, body });
  } catch (error) {
    output.textContent = [
      `Check: ${config.title}`,
      `Request: ${config.method} ${config.path}`,
      "Result: request could not be completed",
      "",
      error.message,
    ].join("\n");
  }
}

if ($("#missing-token")) $("#missing-token").addEventListener("click", async () => {
  await runAttackCheck({
    title: "Missing token",
    method: "GET",
    path: "/auth/me",
    sent: "No Authorization header",
    expected: 401,
    control: "Protected endpoints reject requests without a JWT.",
  });
});

if ($("#bad-token")) $("#bad-token").addEventListener("click", async () => {
  await runAttackCheck({
    title: "Bad token",
    method: "GET",
    path: "/auth/me",
    sent: "Authorization: Bearer invalid-token",
    expected: 401,
    control: "JWT verification rejects malformed or forged tokens.",
  }, { headers: { Authorization: "Bearer invalid-token" } });
});

if ($("#bad-input")) $("#bad-input").addEventListener("click", async () => {
  await runAttackCheck({
    title: "Bad input",
    method: "POST",
    path: "/library/books",
    sent: "isbn='bad', total_copies=-1",
    expected: 422,
    control: "Pydantic validation blocks malformed book data before database writes.",
  }, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ title: "Bad", author: "Bad", isbn: "bad", total_copies: -1 }),
  });
});

async function refreshUserBooks() {
  try {
    const [books, loans, reservations] = await Promise.all([
      api("/library/books"),
      api("/loans"),
      api("/reservations"),
    ]);
    const userBooksEl = $("#user-books-list");
    renderUserBookList(mergeUserBookState(books, loans, reservations), userBooksEl, "No books available.");
  } catch (error) {
    $("#user-books-list").innerHTML = `<article class="book-row"><span>${error.message}</span></article>`;
  }
}

async function refreshBorrowedBooks() {
  try {
    const loans = await api("/loans");
    const borrowedEl = $("#borrowed-books-list");
    borrowedEl.innerHTML = loans.length
      ? loans.map((loan) => `
        <article class="book-row loan-row">
          <div class="book-info">
            <strong>${loan.title}</strong>
            <span>${loan.author || "Unknown author"}</span>
          </div>
          <div class="loan-meta">
            <span>Borrowed ${new Date(loan.created_at).toLocaleDateString()}</span>
            <span>Due ${new Date(loan.due_date).toLocaleDateString()}</span>
          </div>
          <div class="book-actions">
            <span class="status">${loan.status}</span>
            ${loan.status === "active" ? `<button class="quiet return-loan-btn" data-loan-id="${loan.id}" type="button">Return</button>` : ""}
          </div>
        </article>`).join("")
      : `<article class="book-row"><span>No borrowed books.</span></article>`;
  } catch (error) {
    $("#borrowed-books-list").innerHTML = `<article class="book-row"><span>${error.message}</span></article>`;
  }
}

async function refreshReservations() {
  if (!$("#reservations-list")) {
    return;
  }
  try {
    const reservations = await api("/reservations");
    const reservationsEl = $("#reservations-list");
    reservationsEl.innerHTML = reservations.length
      ? reservations.map((res) => `
        <article class="book-row loan-row">
          <div class="book-info">
            <strong>${res.title}</strong>
            <span>Request submitted ${new Date(res.reservation_date).toLocaleDateString()}</span>
          </div>
          <div class="loan-meta">
            <span>Reservation</span>
          </div>
          <span class="status">${res.status}</span>
        </article>`).join("")
      : `<article class="book-row"><span>No reservations.</span></article>`;
  } catch (error) {
    $("#reservations-list").innerHTML = `<article class="book-row"><span>${error.message}</span></article>`;
  }
}

async function loadProfile() {
  if (state.user && $("#profile-name") && $("#profile-email")) {
    $("#profile-name").value = state.user.full_name || "";
    $("#profile-email").value = state.user.email || "";
  }
}

if ($("#refresh-user-books")) $("#refresh-user-books").addEventListener("click", refreshUserBooks);

$("#search-btn").addEventListener("click", async () => {
  const query = $("#search-books").value.trim();
  if (!isAdmin()) {
    showView("library");
  }
  if (!query) {
    await refreshUserBooks();
    return;
  }
  try {
    const [books, loans, reservations] = await Promise.all([
      api(`/library/books?search=${encodeURIComponent(query)}`),
      api("/loans"),
      api("/reservations"),
    ]);
    const userBooksEl = $("#user-books-list");
    renderUserBookList(mergeUserBookState(books, loans, reservations), userBooksEl, `No books found for "${query}".`);
  } catch (error) {
    $("#user-books-list").innerHTML = `<article class="book-row"><span>${error.message}</span></article>`;
  }
});

document.addEventListener("click", async (event) => {
  if (event.target.classList.contains("borrow-btn")) {
    const bookId = event.target.dataset.bookId;
    try {
      await api("/reservations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: bookId }),
      });
      toast("Borrow request sent for admin approval");
      await refreshAll();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("edit-book-btn")) {
    const button = event.target;
    const title = prompt("Book title:", button.dataset.title);
    if (!title) return;
    const author = prompt("Author:", button.dataset.author);
    if (!author) return;
    const total = Number(prompt("Total copies:", button.dataset.total));
    if (Number.isNaN(total)) return;
    const category = prompt("Category:", button.dataset.category || "") || "";
    try {
      await api(`/library/books/${button.dataset.bookId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, author, total_copies: total, category }),
      });
      toast("Book updated");
      await refreshAll();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("delete-book-btn")) {
    if (event.target.dataset.confirmDelete !== "true") {
      event.target.dataset.confirmDelete = "true";
      event.target.textContent = "Confirm delete";
      event.target.classList.add("danger-btn");
      setTimeout(() => {
        if (event.target.dataset.confirmDelete === "true") {
          event.target.dataset.confirmDelete = "false";
          event.target.textContent = "Delete";
          event.target.classList.remove("danger-btn");
        }
      }, 3500);
      return;
    }
    try {
      await api(`/library/books/${event.target.dataset.bookId}`, { method: "DELETE" });
      toast("Book deleted");
      await refreshAll();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("user-active-btn")) {
    const isActive = event.target.dataset.active === "true";
    try {
      await api(`/auth/admin/users/${event.target.dataset.userId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !isActive }),
      });
      toast(isActive ? "Account suspended" : "Account activated");
      await refreshUsers();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("user-delete-btn")) {
    if (!confirm(`Delete user ${event.target.dataset.email}?`)) return;
    try {
      await api(`/auth/admin/users/${event.target.dataset.userId}`, { method: "DELETE" });
      toast("User deleted");
      await refreshAll();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("reservation-decision")) {
    try {
      const payload = { status: event.target.dataset.status };
      await api(`/loans/admin/reservations/${event.target.dataset.reservationId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast("Request updated");
      await refreshAll();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("reserve-btn")) {
    const bookId = event.target.dataset.bookId;
    try {
      await api("/reservations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: bookId }),
      });
      toast("Reservation request sent for admin approval");
      await refreshAll();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("return-loan-btn")) {
    try {
      await api(`/loans/${event.target.dataset.loanId}/return`, { method: "POST" });
      toast("Book returned");
      await refreshAll();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("admin-return-loan-btn")) {
    try {
      await api(`/loans/admin/loans/${event.target.dataset.loanId}/return`, { method: "POST" });
      toast("Loan returned by admin");
      await refreshAll();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("admin-delete-loan-btn")) {
    try {
      await api(`/loans/admin/loans/${event.target.dataset.loanId}`, { method: "DELETE" });
      toast("Loan record deleted");
      await refreshAll();
    } catch (error) {
      toast(error.message);
    }
  } else if (event.target.classList.contains("document-download-btn")) {
    try {
      await authenticatedDownload(
        `/library/documents/${event.target.dataset.documentId}/download`,
        event.target.dataset.filename
      );
      toast("Download started");
    } catch (error) {
      toast(error.message);
    }
  }
});

if ($("#change-password-btn")) $("#change-password-btn").addEventListener("click", () => {
  const newPassword = prompt("Enter new password (min 10 characters):");
  if (newPassword && newPassword.length >= 10) {
    // TODO: Implement password change
    toast("Password change not implemented yet");
  }
});

if ($("#refresh-users")) $("#refresh-users").addEventListener("click", refreshUsers);
if ($("#refresh-loans")) $("#refresh-loans").addEventListener("click", refreshAdminBorrowing);
if ($("#refresh-documents")) $("#refresh-documents").addEventListener("click", refreshDocuments);

function addChatMessage(role, text) {
  const messages = $("#ai-messages");
  const message = document.createElement("div");
  message.className = `chat-message ${role}`;
  message.textContent = text;
  messages.appendChild(message);
  messages.scrollTop = messages.scrollHeight;
}

async function askAi(question) {
  if (!question) {
    toast("Ask a question first");
    return;
  }
  addChatMessage("user", question);
  $("#ai-question").value = "";
  addChatMessage("assistant", "Thinking...");
  try {
    const data = await api("/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const pending = $("#ai-messages .chat-message.assistant:last-child");
    const sources = data.sources.map((source) => `${source.title} (${Math.round(source.score * 100)}%)`).join(", ");
    pending.textContent = sources ? `${data.answer}\nSources: ${sources}` : data.answer;
  } catch (error) {
    const pending = $("#ai-messages .chat-message.assistant:last-child");
    pending.textContent = error.message;
    toast(error.message);
  }
}

if ($("#ai-chat-form")) $("#ai-chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await askAi($("#ai-question").value.trim());
});

document.querySelectorAll(".quick-prompt").forEach((button) => {
  button.addEventListener("click", async () => askAi(button.dataset.question));
});

document.querySelectorAll("[data-view-link]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    showView(link.dataset.viewLink);
  });
});

window.addEventListener("hashchange", () => showView(viewFromHash()));

document.addEventListener("change", async (event) => {
  if (!event.target.classList.contains("role-select")) return;
  try {
    await api(`/auth/admin/users/${event.target.dataset.userId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: event.target.value }),
    });
    toast("Role updated");
    await refreshUsers();
  } catch (error) {
    toast(error.message);
    await refreshUsers();
  }
});

async function refreshAdminBorrowing() {
  if (!isAdmin()) return;
  try {
    const [loans, reservations] = await Promise.all([
      api("/loans/admin/loans"),
      api("/loans/admin/reservations"),
    ]);
    const statusWeight = { pending: 0, approved: 1, rejected: 2 };
    const sortedReservations = [...reservations].sort((left, right) => {
      const statusDiff = (statusWeight[left.status] ?? 9) - (statusWeight[right.status] ?? 9);
      if (statusDiff !== 0) return statusDiff;
      return new Date(right.created_at) - new Date(left.created_at);
    });
    $("#admin-reservations-list").innerHTML = `
      <div class="list-label">Requests first</div>
      ${sortedReservations.length
        ? sortedReservations.map((reservation) => `
          <article class="book-row request-row" data-status="${reservation.status}">
            <div class="book-info">
              <strong>${reservation.title}</strong>
              <span>${reservation.full_name || reservation.email}</span>
              <small>${new Date(reservation.created_at).toLocaleString()}</small>
            </div>
            <span class="status">${reservation.status}</span>
            ${reservation.status === "pending" ? `
              <div class="book-actions">
                <button class="quiet reservation-decision" data-reservation-id="${reservation.id}" data-status="approved" type="button">Approve</button>
                <button class="quiet reservation-decision" data-reservation-id="${reservation.id}" data-status="rejected" type="button">Reject</button>
              </div>` : `<span class="status-note">Decided</span>`}
          </article>`).join("")
        : `<article class="book-row"><span>No reservation requests.</span></article>`}`;
    $("#admin-loans-list").innerHTML = loans.length
      ? `<div class="list-label">Loans</div>${loans.map((loan) => `
        <article class="book-row">
          <div class="book-info">
            <strong>${loan.title}</strong>
            <span>${loan.full_name || loan.email}</span>
            <small>Due: ${new Date(loan.due_date).toLocaleDateString()}</small>
          </div>
          <span class="status">${loan.overdue ? "Late" : loan.status}</span>
          <div class="book-actions">
            ${loan.status === "active" ? `<button class="quiet admin-return-loan-btn" data-loan-id="${loan.id}" type="button">Force return</button>` : ""}
            <button class="quiet danger-btn admin-delete-loan-btn" data-loan-id="${loan.id}" type="button">Delete</button>
          </div>
        </article>`).join("")}`
      : `<div class="list-label">Loans</div><article class="book-row"><span>No loans yet.</span></article>`;
  } catch (error) {
    $("#admin-loans-list").innerHTML = `<article class="book-row"><span>${error.message}</span></article>`;
  }
}

async function refreshDocuments() {
  if (!isAdmin() || !$("#documents-list")) return;
  try {
    const documents = await api("/library/documents");
    $("#documents-list").innerHTML = documents.length
      ? documents.map((document) => `
        <article class="document-row">
          <div class="book-info">
            <strong>${document.original_name}</strong>
            <span>${document.owner_email || "Current user"}</span>
            <small>${Math.ceil(document.size_bytes / 1024)} KB | ${new Date(document.created_at).toLocaleString()}</small>
          </div>
          <span class="status">${document.processing_status}</span>
          <button class="quiet document-download-btn" data-document-id="${document.id}" data-filename="${document.original_name}" type="button">Download</button>
        </article>`).join("")
      : `<article class="book-row"><span>No uploaded documents yet.</span></article>`;
  } catch (error) {
    $("#documents-list").innerHTML = `<article class="book-row"><span>${error.message}</span></article>`;
  }
}

$("#logout-btn").addEventListener("click", async () => {
  try {
    if (state.token) {
      await api("/auth/logout", { method: "POST" });
    }
  } catch {
    // Logout should still clear the local session if the token is already expired.
  } finally {
    setSession("");
  }
});

async function boot() {
  const bootBookForm = $("#book-form");
  const isbnInput = bootBookForm ? bootBookForm.elements.namedItem("isbn") : null;
  if (isbnInput) {
    isbnInput.value = randomIsbn();
  }

  // Check authentication
  if (!state.token) {
    window.location.href = "/login.html";
    return;
  }

  // Verify token is still valid
  try {
    state.user = await api("/auth/me");
    renderSession();
  } catch (error) {
    sessionStorage.removeItem("secureLibraryToken");
    localStorage.removeItem("secureLibraryToken");
    window.location.href = "/login.html";
    return;
  }

  try {
    const response = await fetch("/");
    if (!response.ok) {
      toast("Gateway degraded");
    }
  } catch {
    toast("Gateway offline");
  }
  await refreshAll();
}

boot();

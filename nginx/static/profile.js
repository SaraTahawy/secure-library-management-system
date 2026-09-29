const $ = (selector) => document.querySelector(selector);
const toastEl = $("#toast");
const token = sessionStorage.getItem("secureLibraryToken") || localStorage.getItem("secureLibraryToken") || "";

function toast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  setTimeout(() => toastEl.classList.remove("show"), 2600);
}

function clearSession() {
  sessionStorage.removeItem("secureLibraryToken");
  localStorage.removeItem("secureLibraryToken");
  window.location.href = "/login.html";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  const text = await response.text();
  let data = text;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = text;
  }
  if (!response.ok) {
    const message = typeof data === "object" ? JSON.stringify(data) : data;
    throw new Error(`${response.status} ${message}`);
  }
  return data;
}

$("#logout-btn").addEventListener("click", async () => {
  try {
    if (token) {
      await api("/auth/logout", { method: "POST" });
    }
  } catch {
    // Clear the browser session even when the token is expired.
  } finally {
    clearSession();
  }
});

$("#change-password-btn").addEventListener("click", async () => {
  const newPassword = $("#new-password").value;
  if (newPassword.length < 10) {
    toast("Password must be at least 10 characters");
    return;
  }
  try {
    await api("/auth/profile/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_password: newPassword }),
    });
    toast("Password changed. Please login again.");
    setTimeout(clearSession, 900);
  } catch (error) {
    toast(error.message);
  }
});

async function boot() {
  if (!token) {
    clearSession();
    return;
  }
  try {
    const user = await api("/auth/me");
    sessionStorage.setItem("secureLibraryToken", token);
    localStorage.removeItem("secureLibraryToken");
    $("#profile-name").value = user.full_name || "";
    $("#profile-email").value = user.email || "";
  } catch (error) {
    clearSession();
  }
}

boot();

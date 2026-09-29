const $ = (selector) => document.querySelector(selector);
const toastEl = $("#toast");

function toast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  setTimeout(() => toastEl.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.headers || {},
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

function setSession(token) {
  if (token) {
    sessionStorage.setItem("secureLibraryToken", token);
    localStorage.removeItem("secureLibraryToken");
    window.location.href = "/";
  }
}

const oauthToken = new URLSearchParams(window.location.search).get("token");
if (oauthToken) {
  window.history.replaceState({}, document.title, "/login.html");
  setSession(oauthToken);
}

// Tab switching
document.querySelectorAll("[data-auth-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-auth-tab]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    $("#login-form").classList.toggle("hidden", button.dataset.authTab !== "login");
    $("#register-form").classList.toggle("hidden", button.dataset.authTab !== "register");
  });
});

// Login form submission
$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const data = await api("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(form)),
    });
    setSession(data.access_token);
    toast("Login successful!");
  } catch (error) {
    toast(error.message);
  }
});

// Register form submission
$("#register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await api("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(form)),
    });
    toast("Account created. Please login.");
    // Switch to login tab
    document.querySelector('[data-auth-tab="login"]').click();
  } catch (error) {
    toast(error.message);
  }
});

// Check if already logged in
async function checkAuth() {
  const token = sessionStorage.getItem("secureLibraryToken") || localStorage.getItem("secureLibraryToken");
  if (token) {
    try {
      await api("/auth/me", {
        headers: { Authorization: `Bearer ${token}` },
      });
      sessionStorage.setItem("secureLibraryToken", token);
      localStorage.removeItem("secureLibraryToken");
      window.location.href = "/";
    } catch (error) {
      // Token invalid, stay on login page
      sessionStorage.removeItem("secureLibraryToken");
      localStorage.removeItem("secureLibraryToken");
    }
  }
}

checkAuth();

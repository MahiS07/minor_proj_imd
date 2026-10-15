const VERIFICATION_CATEGORY_LABELS = {
    takeoff: 'Take-Off Forecast',
    local_area: 'Local Area Forecast',
    aerodrome: 'Aerodrome Warning'
};

let verificationParamsLoaded = false;

function setActiveTab(tabName) {
    const panels = document.querySelectorAll('[data-tab-panel]');
    panels.forEach(panel => {
        panel.classList.toggle('hidden', panel.id !== tabName);
    });

    const buttons = document.querySelectorAll('[data-tab-target]');
    buttons.forEach(button => {
        const isActive = button.dataset.tabTarget === tabName;
        button.className = isActive
            ? 'verification-tab-btn bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-semibold'
            : 'verification-tab-btn bg-gray-100 text-gray-700 hover:bg-gray-200 px-4 py-2 rounded-md text-sm font-semibold';
    });

    if (tabName === 'verificationParametersTab' && !verificationParamsLoaded) {
        loadVerificationParams();
    }
}

function setupSuperAdminTabs() {
    const buttons = document.querySelectorAll('[data-tab-target]');
    buttons.forEach(button => {
        button.addEventListener('click', () => setActiveTab(button.dataset.tabTarget));
    });

    const sectionButtons = document.querySelectorAll('.verification-section-toggle');
    sectionButtons.forEach(button => {
        button.addEventListener('click', () => {
            const targetId = button.dataset.sectionTarget;
            const body = document.getElementById(targetId);
            const icon = document.querySelector(`[data-section-icon="${targetId}"]`);
            if (!body) return;
            body.classList.toggle('hidden');
            if (icon) {
                icon.classList.toggle('rotate-180', body.classList.contains('hidden'));
            }
        });
    });

    setActiveTab('userManagementTab');
}

function toggleVerificationParamRow(checkbox) {
    const row = checkbox.closest('.verification-param-row');
    if (!row) return;
    const input = row.querySelector('.verification-param-input');
    if (input) {
        input.disabled = !checkbox.checked;
    }
    row.classList.toggle('opacity-60', !checkbox.checked);
}

function renderVerificationParamRow(category, parameter) {
    const enabled = parameter.is_enabled !== false;
    const value = parameter.param_value ?? '';
    const unit = parameter.unit || '';
    const description = parameter.description || '';
    return `
      <div class="verification-param-row bg-slate-50 border border-slate-200 rounded-xl p-4 flex flex-col space-y-3 shadow-sm hover:shadow transition-shadow" data-param-key="${parameter.param_key}" data-category="${category}">
        <div>
          <p class="font-bold text-gray-800 text-sm tracking-tight break-all">${parameter.param_key}</p>
          <p class="text-xs text-gray-500 font-medium leading-normal mt-0.5">${description}</p>
        </div>
        
        <div>
          <label class="block text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1">Current Value</label>
          <input type="number" min="0" step="any" value="${value}" class="verification-param-input w-full border border-gray-300 px-3 py-1.5 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 font-medium text-gray-800 bg-white" ${enabled ? '' : 'disabled'}>
        </div>
        
        <div>
          <label class="block text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1">Unit</label>
          <div class="w-full border border-gray-200 bg-gray-100 px-3 py-1.5 rounded-md text-sm text-gray-700 font-semibold">${unit || '-'}</div>
        </div>
        
        <div class="pt-1">
          <label class="inline-flex items-center gap-2 text-sm font-semibold text-gray-700 cursor-pointer select-none">
            <input type="checkbox" class="verification-param-enabled h-4 w-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500 transition-colors" ${enabled ? 'checked' : ''} onchange="toggleVerificationParamRow(this)">
            <span class="ml-1.5">Enabled</span>
          </label>
        </div>
      </div>
    `;
}

async function loadVerificationParams() {
    try {
        const res = await fetch('/auth/verification-params', { credentials: 'include' });
        if (!res.ok) throw new Error('Failed to load verification parameters');

        const grouped = await res.json();
        Object.entries(VERIFICATION_CATEGORY_LABELS).forEach(([category]) => {
            const container = document.getElementById(`verificationParams-${category}`);
            if (!container) return;
            const params = grouped?.[category] || [];
            if (!params.length) {
                container.innerHTML = '<p class="text-sm text-gray-500">No parameters found.</p>';
                return;
            }
            container.innerHTML = params.map(parameter => renderVerificationParamRow(category, parameter)).join('');
            container.querySelectorAll('.verification-param-enabled').forEach(checkbox => toggleVerificationParamRow(checkbox));
        });
        verificationParamsLoaded = true;
    } catch (err) {
        Object.keys(VERIFICATION_CATEGORY_LABELS).forEach(category => {
            const container = document.getElementById(`verificationParams-${category}`);
            if (container) container.innerHTML = `<p class="text-red-600 py-4 text-sm">${err.message}</p>`;
        });
    }
}

async function saveVerificationParams(category) {
    const container = document.getElementById(`verificationParams-${category}`);
    if (!container) return;

    const rows = container.querySelectorAll('.verification-param-row');
    const parameters = [];

    for (const row of rows) {
        const paramKey = row.dataset.paramKey;
        const input = row.querySelector('.verification-param-input');
        const enabledInput = row.querySelector('.verification-param-enabled');
        const value = parseFloat(input?.value);

        if (Number.isNaN(value) || value < 0) {
            showAlert(`Enter a valid non-negative value for ${paramKey}`, 'error');
            return;
        }

        parameters.push({
            param_key: paramKey,
            param_value: value,
            is_enabled: Boolean(enabledInput?.checked)
        });
    }

    try {
        const res = await fetch('/auth/verification-params', {
            method: 'PUT',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ category, parameters })
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || 'Failed to save verification parameters');
        }

        verificationParamsLoaded = false;
        await loadVerificationParams();
        showAlert(`${VERIFICATION_CATEGORY_LABELS[category]} parameters saved successfully`, 'success');
    } catch (err) {
        showAlert(err.message, 'error');
    }
}

async function loadAdmins() {
    try {
        const res = await fetch('/auth/admins', { credentials: 'include' });
        if (!res.ok) throw new Error('Failed to load admins');
        
        const admins = await res.json();
        const container = document.getElementById('adminList');
        container.innerHTML = '';

        if (!admins || admins.length === 0) {
            container.innerHTML = '<p class="text-gray-500 py-8 text-center">No admins found</p>';
            return;
        }

        admins.forEach(a => {
            container.innerHTML += `
              <div class="bg-gray-50 p-4 rounded-lg border border-gray-200 flex justify-between items-center">
                <div>
                  <p class="font-semibold text-gray-800">${a.username}</p>
                  <p class="text-xs text-gray-500">${a.role}</p>
                  <p class="text-xs ${a.active ? 'text-green-600' : 'text-red-600'} font-medium">
                    ${a.active ? 'Active' : 'Disabled'}
                  </p>
                </div>
                <div class="flex gap-2">
                  <button onclick="toggleAdminStatus(${a.id}, ${!a.active})" 
                    class="px-3 py-1 text-xs font-medium rounded ${a.active ? 'bg-yellow-100 text-yellow-700 hover:bg-yellow-200' : 'bg-green-100 text-green-700 hover:bg-green-200'}">
                    ${a.active ? 'Disable' : 'Enable'}
                  </button>
                  <button onclick="deleteAdmin(${a.id})"
                    class="px-3 py-1 text-xs font-medium bg-red-100 text-red-700 hover:bg-red-200 rounded">
                    Delete
                  </button>
                </div>
              </div>
            `;
        });
    } catch (err) {
        const container = document.getElementById('adminList');
        container.innerHTML = `<p class="text-red-600 py-8 text-center">${err.message}</p>`;
    }
}

async function loadUsers() {
    try {
        const res = await fetch('/auth/users', { credentials: 'include' });
        if (!res.ok) throw new Error('Failed to load users');
        
        const users = await res.json();
        const container = document.getElementById('userList');
        container.innerHTML = '';

        if (!users || users.length === 0) {
            container.innerHTML = '<p class="text-gray-500 py-8 text-center">No users found</p>';
            return;
        }

        users.forEach(u => {
            container.innerHTML += `
              <div class="bg-gray-50 p-4 rounded-lg border border-gray-200 flex justify-between items-center">
                <div>
                  <p class="font-semibold text-gray-800">${u.username}</p>
                  <p class="text-xs text-gray-500">Station: ${u.station}</p>
                  <p class="text-xs ${u.active ? 'text-green-600' : 'text-red-600'} font-medium">
                    ${u.active ? ' Active' : ' Disabled'}
                  </p>
                </div>
                <div class="flex gap-2">
                  <button onclick="toggleUserStatus(${u.id}, ${!u.active})" 
                    class="px-3 py-1 text-xs font-medium rounded ${u.active ? 'bg-yellow-100 text-yellow-700 hover:bg-yellow-200' : 'bg-green-100 text-green-700 hover:bg-green-200'}">
                    ${u.active ? 'Disable' : 'Enable'}
                  </button>
                  <button onclick="deleteUser(${u.id})"
                    class="px-3 py-1 text-xs font-medium bg-red-100 text-red-700 hover:bg-red-200 rounded">
                    Delete
                  </button>
                </div>
              </div>
            `;
        });
    } catch (err) {
        const container = document.getElementById('userList');
        container.innerHTML = `<p class="text-red-600 py-8 text-center">${err.message}</p>`;
    }
}

async function toggleAdminStatus(id, isActive) {
    try {
        const res = await fetch(`/auth/admins/${id}`, {
            method: 'PUT',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: isActive })
        });

        if (res.ok) {
            showAlert(isActive ? 'Admin enabled' : 'Admin disabled', 'success');
            loadAdmins();
        } else {
            showAlert('Failed to update admin', 'error');
        }
    } catch (err) {
        showAlert('Network error', 'error');
    }
}

async function toggleUserStatus(id, isActive) {
    try {
        const res = await fetch(`/auth/users/${id}`, {
            method: 'PUT',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: isActive })
        });

        if (res.ok) {
            showAlert(isActive ? 'User enabled' : 'User disabled', 'success');
            loadUsers();
        } else {
            showAlert('Failed to update user', 'error');
        }
    } catch (err) {
        showAlert('Network error', 'error');
    }
}

async function deleteAdmin(id) {
    if (!confirm('Are you sure you want to delete this admin?')) return;

    try {
        const res = await fetch(`/auth/remove/${id}`, {
            method: 'DELETE',
            credentials: 'include'
        });

        if (res.ok) {
            showAlert('Admin deleted successfully', 'success');
            loadAdmins();
        } else {
            showAlert('Failed to delete admin', 'error');
        }
    } catch (err) {
        showAlert('Network error', 'error');
    }
}

async function deleteUser(id) {
    if (!confirm('Are you sure you want to delete this user?')) return;

    try {
        const res = await fetch(`/auth/remove/${id}`, {
            method: 'DELETE',
            credentials: 'include'
        });

        if (res.ok) {
            showAlert('User deleted successfully', 'success');
            loadUsers();
        } else {
            showAlert('Failed to delete user', 'error');
        }
    } catch (err) {
        showAlert('Network error', 'error');
    }
}

function showAlert(message, type) {
    const alertBox = document.getElementById('alertBox');
    if (alertBox) {
        alertBox.textContent = message;
        alertBox.className = `${type === 'success' ? 'bg-green-500' : 'bg-red-500'} fixed top-4 right-4 p-4 rounded-md shadow-md text-white max-w-sm z-50`;
        alertBox.classList.remove('hidden');
        setTimeout(() => alertBox.classList.add('hidden'), 3000);
    }
}

// Wait for auth to finish before initial load
if (window.authManager && window.authManager.ready) {
    window.authManager.ready.then(() => {
        authManager.requireRole('super_admin');
        if (authManager.user) {
            const avatar = document.getElementById('userAvatar');
            const info = document.getElementById('userInfo');
            const dropdown = document.getElementById('userInfoDropdown');
            if (avatar) avatar.textContent = authManager.user.username.charAt(0).toUpperCase();
            if (info) { info.textContent = authManager.user.username; info.style.display = 'inline'; }
            if (dropdown) dropdown.textContent = authManager.user.username;
        }
        setupSuperAdminTabs();
        loadAdmins();
        loadUsers();
        loadVerificationParams();
    }).catch(() => {});
} else {
    setupSuperAdminTabs();
    loadAdmins();
    loadUsers();
    loadVerificationParams();
}
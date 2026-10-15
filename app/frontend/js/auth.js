// function generateTabId() {
//     let tabId = sessionStorage.getItem('tab_id');
//     if (!tabId) {
//         tabId = 'tab_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now();
//         sessionStorage.setItem('tab_id', tabId);
//     }
//     return tabId;
// }

// const TAB_ID = generateTabId();

class AuthManager {
    constructor() {
        this.user = null;
        this.ready = this.init();
    }

    async init() {
        // Check if this is a new tab (first time loading in this session)
        // Track last tab but DO NOT delete cookie
        // const previousTabId = localStorage.getItem('tab_id');
        // localStorage.setItem('tab_id', TAB_ID);
        
        // Skip auth check on login/signup/auth pages to prevent redirect loops
        const path = window.location.pathname.toLowerCase();
        const shouldSkip = path.includes('login') || path.includes('signup') || path.includes('auth');
        
        console.log('[AuthManager.init] path:', path, 'shouldSkip:', shouldSkip);
        if (shouldSkip) {
            console.log('[AuthManager] Skipping checkAuth on auth page');
            // this.ready = Promise.resolve();
            return;
        }

        await this.checkAuth();
    }

    async checkAuth() {
        try {
            // Double-check we're not on a login/signup page
            const path = window.location.pathname.toLowerCase();
            if (path.includes('login') || path.includes('signup')) {
                console.log('[checkAuth] ABORT: on auth page, skipping /auth/me call');
                return false;
            }

            // const storedToken = sessionStorage.getItem('auth_token');
            // // const headers = {
            // //     'X-Tab-ID': TAB_ID
            // // };
            // if (storedToken) {
            //     headers['Authorization'] = 'Bearer ' + storedToken;
            // }

            // console.log('[checkAuth] Calling /auth/me with TAB_ID:', TAB_ID, 'hasToken:', !!storedToken);
            // console.log('[checkAuth] Outgoing headers:', headers);
            // console.log('[checkAuth] document.cookie:', document.cookie || '(empty)');

            const response = await fetch('/auth/me', {
                credentials: 'include',
                // headers: headers
            });

            console.log('[checkAuth] /auth/me response status:', response.status);

            if (response.ok) {
                const data = await response.json();
                console.log('[checkAuth] Auth success, user:', data.user);
                this.user = data.user;
                this.updateUI();
                this.routeByRole();
                return true;
            } else {
                console.log('[checkAuth] Auth failed (401/403), redirecting to login');
                this.user = null;
                this.handleUnauthorized();
                return false;
            }
        } catch (err) {
            console.error('[checkAuth] Auth check failed:', err);
            this.user = null;
            this.handleUnauthorized();
            return false;
        }
    }

    async login(username, password) {
        try {
            console.log('[login] Starting login for:', username);
            const response = await fetch('/auth/login', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    // 'X-Tab-ID': TAB_ID
                },
                credentials: 'include',
                body: JSON.stringify({ username, password})
            });

            const data = await response.json();
            console.log('[login] /auth/login response status:', response.status, 'has token:', !!data.token);

            if (!response.ok) {
                console.log('[login] Login failed:', data.error);
                return { success: false, error: data.error || 'Login failed' };
            }

            // Store token in sessionStorage as a fallback for Authorization header
            // if (data && data.token) {
            //     sessionStorage.setItem('auth_token', data.token);
            //     console.log('[login] Token stored in sessionStorage');
            // } else {
            //     console.warn('[login] No token in response!');
            // }

            // Redirect after successful login so browser navigation includes the cookie
            const role = data.role || 'user';
            console.log('[login] Login successful, redirecting to:', role === 'super_admin' ? '/superadmin' : role === 'admin' ? '/admin' : '/');
            if (role === 'super_admin') window.location.replace('/superadmin');
            else if (role === 'admin') window.location.replace('/admin');
            else window.location.replace('/');
            return { success: true };

        } catch (err) {
            console.error('[login] Error:', err);
            return { success: false, error: 'Network error' };
        }
    }

    async logout() {
        // Show confirmation dialog
        const confirmLogout = confirm('Are you sure you want to logout?');
        
        if (!confirmLogout) {
            return; // User cancelled logout
        }
        
        try {
            await fetch('/auth/logout', {
                method: 'POST',
                credentials: 'include',
                headers: {
                    // 'X-Tab-ID': TAB_ID,
                    'Content-Type': 'application/json'
                }
            });
        } finally {
            this.user = null;
            // clear any client-side storage if needed
            localStorage.removeItem('user');
            sessionStorage.removeItem('auth_token');
            // clear cookie as well for extra safety
            // document.cookie = "auth_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
            window.location.href = '/login';
        }
    }

    isAuthenticated() {
        return !!this.user;
    }

    hasRole(role) {
        if (!this.user) return false;

        const hierarchy = {
            user: 1,
            admin: 2,
            super_admin: 3
        };

        return hierarchy[this.user.role] >= hierarchy[role];
    }

    isAdmin() {
        return this.hasRole('admin');
    }

    isSuperAdmin() {
        return this.hasRole('super_admin');
    }

    updateUI() {
        const userInfo = document.getElementById('userInfo');
        const logoutBtn = document.getElementById('logoutBtn');
        const userRoleEl = document.getElementById('userRole');
        const avatarEl = document.getElementById('userAvatar');

        if (this.user) {
            if (userInfo) {
                userInfo.textContent = this.user.username;
                userInfo.style.display = 'inline';
            }
            if (userRoleEl) {
                userRoleEl.textContent = this.user.role;
            }
            if (avatarEl) {
                // show first letter as avatar fallback
                avatarEl.textContent = (this.user.username || 'U').charAt(0).toUpperCase();
            }
            if (logoutBtn) logoutBtn.style.display = 'inline';
        } else {
            if (userInfo) userInfo.style.display = 'none';
            if (userRoleEl) userRoleEl.textContent = '';
            if (avatarEl) avatarEl.textContent = '';
            if (logoutBtn) logoutBtn.style.display = 'none';
        }
    }

routeByRole() {
    if (!this.user) return;

    const currentPath = window.location.pathname.replace(/\/+$/, '');

    let targetPath = '/';

    if (this.user.role === 'super_admin') {
        targetPath = '/superadmin';
    } else if (this.user.role === 'admin') {
        targetPath = '/admin';
    }

    targetPath = targetPath.replace(/\/+$/, '');

    if (currentPath !== targetPath) {
        console.log('[routeByRole] Redirecting to:', targetPath);
        window.location.href = targetPath;
    }
}



    handleUnauthorized() {
        const path = window.location.pathname;
        if (path !== '/login' && path !== '/signup') {
            window.location.href = '/login';
        }
    }

    requireAuth() {
        if (!this.isAuthenticated()) {
            window.location.href = '/login';
            return false;
        }
        return true;
    }

    requireRole(role) {
        if (!this.requireAuth()) return false;

        if (!this.hasRole(role)) {
            alert('Access denied');
            window.location.href = '/';
            return false;
        }
        return true;
    }
}

const authManager = new AuthManager();
window.authManager = authManager;
import { jsx } from "react/jsx-runtime";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState
} from "react";
import { ApiError, api, authToken } from "../lib/api";

const AuthContext = createContext(null);

function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isBillingAdmin, setIsBillingAdmin] = useState(false);
  const [billingAdminChecked, setBillingAdminChecked] = useState(false);

  const checkBillingAdminAccess = useCallback(async () => {
    setBillingAdminChecked(false);

    try {
      const result = await api.adminBillingAccess();
      setIsBillingAdmin(result?.allowed === true);
    } catch {
      // Fail closed: unavailable or denied access never grants admin access.
      setIsBillingAdmin(false);
    } finally {
      setBillingAdminChecked(true);
    }
  }, []);

  const signOut = useCallback(() => {
    authToken.clear();
    setUser(null);
    setIsBillingAdmin(false);
    setBillingAdminChecked(false);
  }, []);

  const refreshUser = useCallback(async () => {
    if (!authToken.read()) {
      setUser(null);
      setIsBillingAdmin(false);
      setBillingAdminChecked(true);
      return;
    }

    try {
      const currentUser = await api.me();
      setUser(currentUser);
      await checkBillingAdminAccess();
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) throw error;

      authToken.clear();
      setUser(null);
      setIsBillingAdmin(false);
      setBillingAdminChecked(false);
    }
  }, [checkBillingAdminAccess]);

  useEffect(() => {
    const onExpired = () => {
      setUser(null);
      setIsBillingAdmin(false);
      setBillingAdminChecked(false);
    };

    window.addEventListener("auth:expired", onExpired);

    void refreshUser()
      .catch(() => {
        authToken.clear();
        setUser(null);
        setIsBillingAdmin(false);
        setBillingAdminChecked(false);
      })
      .finally(() => setLoading(false));

    return () => window.removeEventListener("auth:expired", onExpired);
  }, [refreshUser]);

  const signIn = useCallback(async (email, password) => {
    const result = await api.login(email, password);
    authToken.write(result.access_token);

    const currentUser = await api.me();
    setUser(currentUser);

    await checkBillingAdminAccess();
  }, [checkBillingAdminAccess]);

  const signUp = useCallback(
    async (name, email, password) => {
      await api.register(name, email, password);
      await signIn(email, password);
    },
    [signIn]
  );

  const value = useMemo(
    () => ({
      user,
      loading,
      isBillingAdmin,
      billingAdminChecked,
      signIn,
      signUp,
      signOut,
      refreshUser,
      updateUser: setUser
    }),
    [
      user,
      loading,
      isBillingAdmin,
      billingAdminChecked,
      signIn,
      signUp,
      signOut,
      refreshUser
    ]
  );

  return jsx(AuthContext.Provider, { value, children });
}

function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }

  return context;
}

export {
  AuthProvider,
  useAuth
};
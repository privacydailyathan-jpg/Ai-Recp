
import { jsx, jsxs } from "react/jsx-runtime";
import {
  Activity,
  AudioLines,
  Building2,
  ChevronDown,
  ChevronsUpDown,
  CreditCard,
  Headphones,
  LayoutDashboard,
  LogOut,
  Menu,
  Phone,
  ShieldCheck,
  Settings,
  Users,
  X
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useBusiness } from "../business/BusinessContext";
import { ErrorState, LoadingState } from "./ui";

const navigation = [
  { to: "/dashboard", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/businesses", label: "Businesses", icon: Building2 },
  { to: "/onboarding", label: "AI receptionist", icon: AudioLines },
  { to: "/calls", label: "Call history", icon: Phone },
  { to: "/billing", label: "Billing", icon: CreditCard },
  { to: "/admin/billing", label: "Billing admin", icon: ShieldCheck }
];

function AppLayout() {
  const { user, signOut, isBillingAdmin } = useAuth();

  const {
    businesses,
    activeBusiness,
    activeBusinessId,
    loading,
    error,
    selectBusiness,
    refreshBusinesses
  } = useBusiness();

  const location = useLocation();
  const navigate = useNavigate();

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [businessMenuOpen, setBusinessMenuOpen] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);

  const visibleNavigation = navigation.filter(
    (item) => item.to !== "/admin/billing" || isBillingAdmin
  );

  const pageTitle = visibleNavigation.find(
    (item) =>
      item.end
        ? location.pathname === item.to
        : location.pathname.startsWith(item.to)
  )?.label ?? (
    location.pathname.startsWith("/settings")
      ? "Settings"
      : location.pathname.startsWith("/admin/billing")
        ? "Workspace"
        : "Workspace"
  );

  const logout = () => {
    signOut();
    navigate("/login", { replace: true });
  };

  return jsxs("div", {
    className: "app-shell",
    children: [
      sidebarOpen && jsx("button", {
        className: "mobile-backdrop",
        "aria-label": "Close navigation",
        onClick: () => setSidebarOpen(false)
      }),

      jsxs("aside", {
        className: `sidebar ${sidebarOpen ? "sidebar-open" : ""}`,
        children: [
          jsxs("div", {
            className: "brand-row",
            children: [
              jsx("div", {
                className: "brand-mark",
                children: jsx(Headphones, {
                  size: 18,
                  strokeWidth: 2.2
                })
              }),
              jsx("span", {
                className: "brand-name",
                children: "relay"
              }),
              jsx("button", {
                className: "icon-button sidebar-close",
                "aria-label": "Close navigation",
                onClick: () => setSidebarOpen(false),
                children: jsx(X, { size: 18 })
              })
            ]
          }),

          jsxs("div", {
            className: "workspace-picker-wrap",
            children: [
              jsxs("button", {
                className: "workspace-picker",
                onClick: () => setBusinessMenuOpen((open) => !open),
                "aria-expanded": businessMenuOpen,
                children: [
                  jsx("span", {
                    className: "workspace-avatar",
                    children: activeBusiness?.name.slice(0, 1).toUpperCase() ?? "R"
                  }),
                  jsxs("span", {
                    className: "workspace-text",
                    children: [
                      jsx("strong", {
                        children: activeBusiness?.name ?? "Your workspace"
                      }),
                      jsx("small", {
                        children: businesses.length === 1
                          ? "Workspace"
                          : `${businesses.length} workspaces`
                      })
                    ]
                  }),
                  jsx(ChevronsUpDown, {
                    size: 15,
                    className: "muted-icon"
                  })
                ]
              }),

              businessMenuOpen && jsxs("div", {
                className: "popover business-popover",
                children: [
                  jsx("div", {
                    className: "popover-caption",
                    children: "Workspaces"
                  }),

                  businesses.map((business) =>
                    jsxs("button", {
                      className: `popover-item ${business.id === activeBusinessId ? "selected" : ""}`,
                      onClick: () => {
                        selectBusiness(business.id);
                        setBusinessMenuOpen(false);
                        setSidebarOpen(false);
                      },
                      children: [
                        jsx("span", {
                          className: "workspace-avatar small",
                          children: business.name.slice(0, 1).toUpperCase()
                        }),
                        jsx("span", {
                          children: business.name
                        })
                      ]
                    }, business.id)
                  ),

                  jsxs("button", {
                    className: "popover-item",
                    onClick: () => {
                      navigate("/businesses");
                      setBusinessMenuOpen(false);
                    },
                    children: [
                      jsx(Building2, { size: 16 }),
                      "Manage businesses"
                    ]
                  })
                ]
              })
            ]
          }),

          jsx("div", {
            className: "nav-section-label",
            children: "Workspace"
          }),

          jsx("nav", {
            className: "main-nav",
            "aria-label": "Main navigation",
            children: visibleNavigation.map(({ to, label, icon: Icon, end }) =>
              jsxs(NavLink, {
                to,
                end,
                className: ({ isActive }) =>
                  `nav-link ${isActive ? "nav-link-active" : ""}`,
                onClick: () => setSidebarOpen(false),
                children: [
                  jsx(Icon, {
                    size: 17,
                    strokeWidth: 1.9
                  }),
                  jsx("span", { children: label })
                ]
              }, to)
            )
          }),

          jsx("div", {
            className: "nav-section-label settings-label",
            children: "Account"
          }),

          jsxs("nav", {
            className: "main-nav",
            "aria-label": "Account navigation",
            children: [
              jsxs(NavLink, {
                to: "/settings",
                className: ({ isActive }) =>
                  `nav-link ${isActive ? "nav-link-active" : ""}`,
                onClick: () => setSidebarOpen(false),
                children: [
                  jsx(Settings, {
                    size: 17,
                    strokeWidth: 1.9
                  }),
                  jsx("span", { children: "Settings" })
                ]
              }),

              jsxs(NavLink, {
                to: "/team",
                className: ({ isActive }) =>
                  `nav-link ${isActive ? "nav-link-active" : ""}`,
                onClick: () => setSidebarOpen(false),
                children: [
                  jsx(Users, {
                    size: 17,
                    strokeWidth: 1.9
                  }),
                  jsx("span", { children: "Team" })
                ]
              })
            ]
          }),

          jsxs("div", {
            className: "sidebar-bottom",
            children: [
              jsxs("div", {
                className: "help-panel",
                children: [
                  jsx("span", {
                    className: "help-icon",
                    children: jsx(Activity, { size: 15 })
                  }),
                  jsxs("div", {
                    children: [
                      jsx("strong", { children: "Need a hand?" }),
                      jsx("p", {
                        children: "Explore your workspace setup."
                      })
                    ]
                  }),
                  jsx(ChevronDown, {
                    size: 14,
                    className: "help-chevron"
                  })
                ]
              }),

              jsxs("div", {
                className: "sidebar-user",
                children: [
                  jsx("span", {
                    className: "user-avatar",
                    children: user?.full_name.slice(0, 1).toUpperCase() ?? "U"
                  }),
                  jsxs("span", {
                    className: "sidebar-user-copy",
                    children: [
                      jsx("strong", {
                        children: user?.full_name ?? "User"
                      }),
                      jsx("small", {
                        children: user?.email
                      })
                    ]
                  }),
                  jsx("button", {
                    className: "icon-button logout-button",
                    onClick: logout,
                    title: "Log out",
                    "aria-label": "Log out",
                    children: jsx(LogOut, { size: 16 })
                  })
                ]
              })
            ]
          })
        ]
      }),

      jsxs("div", {
        className: "main-column",
        children: [
          jsxs("header", {
            className: "topbar",
            children: [
              jsxs("div", {
                className: "topbar-left",
                children: [
                  jsx("button", {
                    className: "icon-button mobile-menu",
                    "aria-label": "Open navigation",
                    onClick: () => setSidebarOpen(true),
                    children: jsx(Menu, { size: 19 })
                  }),

                  jsxs("div", {
                    className: "breadcrumbs",
                    children: [
                      jsx("span", { children: "Workspace" }),
                      jsx("span", {
                        className: "breadcrumb-divider",
                        children: "/"
                      }),
                      jsx("strong", { children: pageTitle })
                    ]
                  })
                ]
              }),

              jsxs("div", {
                className: "topbar-right",
                children: [
                  businesses.length > 0 && jsxs("label", {
                    className: "topbar-business",
                    children: [
                      jsx("span", {
                        className: "sr-only",
                        children: "Select business"
                      }),
                      jsx(Building2, { size: 15 }),
                      jsx("select", {
                        value: activeBusinessId ?? "",
                        onChange: (event) =>
                          selectBusiness(event.target.value),
                        "aria-label": "Select business",
                        children: businesses.map((business) =>
                          jsx("option", {
                            value: business.id,
                            children: business.name
                          }, business.id)
                        )
                      }),
                      jsx(ChevronDown, { size: 13 })
                    ]
                  }),

                  jsxs("div", {
                    className: "profile-wrap",
                    children: [
                      jsxs("button", {
                        className: "profile-button",
                        onClick: () =>
                          setProfileMenuOpen((open) => !open),
                        "aria-expanded": profileMenuOpen,
                        "aria-label": "Open profile menu",
                        children: [
                          jsx("span", {
                            className: "user-avatar small-avatar",
                            children: user?.full_name.slice(0, 1).toUpperCase() ?? "U"
                          }),
                          jsx(ChevronDown, { size: 14 })
                        ]
                      }),

                      profileMenuOpen && jsxs("div", {
                        className: "popover profile-popover",
                        children: [
                          jsxs("div", {
                            className: "profile-popover-user",
                            children: [
                              jsx("strong", {
                                children: user?.full_name
                              }),
                              jsx("small", {
                                children: user?.email
                              })
                            ]
                          }),

                          jsxs("button", {
                            className: "popover-item",
                            onClick: () => {
                              navigate("/settings");
                              setProfileMenuOpen(false);
                            },
                            children: [
                              jsx(Settings, { size: 16 }),
                              " Account settings"
                            ]
                          }),

                          jsxs("button", {
                            className: "popover-item",
                            onClick: logout,
                            children: [
                              jsx(LogOut, { size: 16 }),
                              " Log out"
                            ]
                          })
                        ]
                      })
                    ]
                  })
                ]
              })
            ]
          }),

          jsx("main", {
            className: "content-area",
            children:
              error && businesses.length === 0 && !loading
                ? jsx("div", {
                    className: "content-state",
                    children: jsx(ErrorState, {
                      message: error,
                      onRetry: () => void refreshBusinesses()
                    })
                  })
                : loading && businesses.length === 0
                  ? jsx("div", {
                      className: "content-state",
                      children: jsx(LoadingState, {
                        label: "Loading workspace"
                      })
                    })
                  : jsx(Outlet, {})
          })
        ]
      }),

      jsx("button", {
        className: "click-catcher",
        "aria-label": "Close menus",
        tabIndex: -1,
        onClick: () => {
          setBusinessMenuOpen(false);
          setProfileMenuOpen(false);
        }
      }),

      jsx("button", {
        className: "sr-only",
        "aria-label": "Refresh businesses",
        onClick: () => void refreshBusinesses(),
        children: "Refresh businesses"
      })
    ]
  });
}

export {
  AppLayout
};

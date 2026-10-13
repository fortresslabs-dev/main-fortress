import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  Gauge, Presentation, ListChecks, ClipboardCheck, GitCompare, Gavel,
  Search, FolderOpen, Activity, BarChart3, Network, GitPullRequest,
  FileText, ChevronDown, PanelLeftClose, PanelLeft, LogOut,
} from 'lucide-react'
import { useAuth } from '../App'

// Grouped vertical navigation. `gate` is a key on AuthContext; an item is
// hidden when the user lacks that capability. No gate → visible to everyone.
// Routes are unchanged from the original Navbar.
const GROUPS = [
  { group: 'Overview', items: [
    { to: '/readiness',           label: 'Readiness',           Icon: Gauge },
    { to: '/dashboard/executive', label: 'Executive',           Icon: Presentation,  gate: 'canEdit' },
  ]},
  { group: 'Regulatory program', items: [
    { to: '/rules',        label: 'Compliance rules', Icon: ListChecks },
    { to: '/obligations',  label: 'Obligations',      Icon: ClipboardCheck },
    { to: '/changes',      label: 'Changes',          Icon: GitCompare },
    { to: '/enforcement',  label: 'Enforcement',      Icon: Gavel },
  ]},
  { group: 'OFAC / SDN screening', items: [
    { to: '/dashboard/search', label: 'Search',        Icon: Search,     gate: 'canScreen' },
    { to: '/dashboard/cases',  label: 'Cases',         Icon: FolderOpen, gate: 'canScreen' },
    { to: '/ofac',             label: 'Engine health', Icon: Activity },
  ]},
  { group: 'Model & performance', items: [
    { to: '/dashboard/rules-perf', label: 'Rules performance',   Icon: BarChart3, gate: 'canApprove' },
    { to: '/dashboard/vector',     label: 'Vector intelligence', Icon: Network,   gate: 'canViewVectorIntel' },
  ]},
  { group: 'Operations', items: [
    { to: '/workflows', label: 'Workflows', Icon: GitPullRequest },
    { to: '/reports',   label: 'Reports',   Icon: FileText },
  ]},
]

export default function Navbar() {
  const auth = useAuth()
  const { role, email, onSignOut } = auth
  const roleKey = (role || 'viewer').toLowerCase()

  const [mini, setMini] = useState(false)
  const [closed, setClosed] = useState({})

  const canSee = (it) => !it.gate || auth[it.gate]

  return (
    <aside className={`ftr-side${mini ? ' mini' : ''}`}>
      <div className="ftr-side-brand">
        <span className="mark">F</span>
        <span className="nm">Fortress <b>Labs</b></span>
        <button
          className="ftr-collapse"
          aria-label={mini ? 'Expand sidebar' : 'Collapse sidebar'}
          onClick={() => setMini(m => !m)}
        >
          {mini ? <PanelLeft size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </div>

      <nav className="ftr-side-nav">
        {GROUPS.map(g => {
          const items = g.items.filter(canSee)
          if (items.length === 0) return null
          const isClosed = !!closed[g.group]
          return (
            <div key={g.group} className="ftr-group">
              <button
                className={`ftr-grp${isClosed ? ' closed' : ''}`}
                onClick={() => setClosed(c => ({ ...c, [g.group]: !c[g.group] }))}
              >
                <span>{g.group}</span>
                <ChevronDown size={14} className="chev" />
              </button>
              {!isClosed && items.map(({ to, label, Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  title={label}
                  className={({ isActive }) => `ftr-item${isActive ? ' active' : ''}`}
                >
                  <Icon size={18} className="ico" />
                  <span className="lbl">{label}</span>
                </NavLink>
              ))}
            </div>
          )
        })}
      </nav>

      <div className="ftr-side-foot">
        <span className={`ftr-role ${roleKey}`}>{role}</span>
        <span className="ftr-email">{email}</span>
        {onSignOut && (
          <button className="ftr-signout" onClick={onSignOut} title="Sign out">
            <LogOut size={15} />
            <span className="lbl">Sign out</span>
          </button>
        )}
      </div>
    </aside>
  )
}

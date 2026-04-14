import { useEffect, useState } from 'react'
import {
  MagnifyingGlassIcon,
  DocumentTextIcon,
  ChevronRightIcon,
  FolderOpenIcon,
  EyeIcon,
  Squares2X2Icon,
  ShieldCheckIcon,
  LockClosedIcon,
  CloudIcon,
  HomeIcon,
  Bars3Icon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import { useAuth } from '../contexts/AuthContext'
import { LocalizedLink } from '../components/LocalizedLink'
import ThemeSwitcher from '../components/ThemeSwitcher'
import LanguageSwitcher from '../components/LanguageSwitcher'

// Asset paths for Quellex tenant
const ASSETS = '/tenants/quellex/assets'

// ─── Quellex Logo ───────────────────────────────────────────
function QuellexLogo({ size = 'default' }: { size?: 'small' | 'default' | 'large' }) {
  const sizeClass = size === 'large' ? 'h-16 sm:h-20' : size === 'small' ? 'h-6' : 'h-8 sm:h-10'
  return (
    <img
      src={`${ASSETS}/quellex-logo.png`}
      alt="Quellex"
      className={`${sizeClass} w-auto object-contain cursor-pointer`}
    />
  )
}

// ─── Mock Interface Card ────────────────────────────────────
function MockInterface() {
  const documents = [
    { name: 'Urteil OGH 3Ob42/24k', date: '14.03.2026', relevance: 98 },
    { name: 'Beschluss LG Wien 27Cg12/25', date: '02.01.2026', relevance: 94 },
    { name: 'Vertrag – Mandant Müller', date: '28.11.2025', relevance: 87 },
    { name: 'Schriftsatz BG Innsbruck', date: '19.09.2025', relevance: 82 },
  ]

  return (
    <div className="quellex-glass-surface rounded-xl overflow-hidden quellex-card-shadow hover:quellex-card-hover-shadow transition-all duration-500 w-full max-w-lg relative">
      {/* Watermark */}
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
        <span className="text-primary-900/[0.12] dark:text-white/[0.12] text-3xl font-serif-display font-bold tracking-[0.25em] rotate-[-18deg] select-none uppercase">
          MUSTER
        </span>
      </div>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-surface-variant/25 dark:border-gray-700/25 bg-surface-variant/10 dark:bg-gray-800/30">
        <img src={`${ASSETS}/quellex-q-icon.png`} alt="Q" className="w-[21px] h-[21px] object-contain" />
        <span className="text-xs text-secondary dark:text-gray-400">Quellex — Aktensuche</span>
      </div>
      {/* Search bar */}
      <div className="p-3 border-b border-surface-variant/15 dark:border-gray-700/15">
        <div className="flex items-center gap-2 bg-surface-variant/40 dark:bg-gray-700/40 rounded-lg px-3 py-2 border border-surface-variant/10 dark:border-gray-600/10">
          <MagnifyingGlassIcon className="w-4 h-4 text-secondary dark:text-gray-400" />
          <span className="text-sm text-secondary dark:text-gray-400">Schadenersatz bei Vertragsverletzung...</span>
        </div>
      </div>
      {/* Document list */}
      <div className="divide-y divide-surface-variant/10 dark:divide-gray-700/10">
        {documents.map((doc, i) => (
          <div
            key={i}
            className={`flex items-center gap-3 px-4 py-3 hover:bg-surface-variant/20 dark:hover:bg-gray-700/20 transition-colors duration-200 cursor-pointer ${i === 0 ? 'bg-surface-variant/15 dark:bg-gray-700/15' : ''}`}
          >
            <DocumentTextIcon className="w-4 h-4 text-quellex-gold shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-primary-900 dark:text-white truncate">{doc.name}</p>
              <p className="text-xs text-secondary dark:text-gray-400">{doc.date}</p>
            </div>
            <span className="text-xs font-semibold text-quellex-teal">{doc.relevance}%</span>
            <ChevronRightIcon className="w-3.5 h-3.5 text-secondary dark:text-gray-400" />
          </div>
        ))}
      </div>
      {/* Preview footer */}
      <div className="border-t border-surface-variant/15 dark:border-gray-700/15 p-4 bg-surface-variant/10 dark:bg-gray-800/30">
        <p className="text-xs font-medium text-quellex-gold mb-2">Vorschau — Urteil OGH 3Ob42/24k</p>
        <div className="space-y-1.5">
          <div className="h-2 bg-surface-variant/30 dark:bg-gray-600/30 rounded w-full" />
          <div className="h-2 bg-surface-variant/30 dark:bg-gray-600/30 rounded w-11/12" />
          <div className="h-2 rounded w-9/12" style={{ background: 'hsl(43 65% 52% / 0.2)' }} />
          <div className="h-2 bg-surface-variant/30 dark:bg-gray-600/30 rounded w-10/12" />
          <div className="h-2 bg-surface-variant/30 dark:bg-gray-600/30 rounded w-7/12" />
        </div>
        <p className="text-xs text-quellex-teal mt-2">Quellenangabe: Seite 12, Abs. 3</p>
      </div>
    </div>
  )
}

// ─── Trust Bar ──────────────────────────────────────────────
const trustBadgeIcons = [MagnifyingGlassIcon, LockClosedIcon, EyeIcon, ShieldCheckIcon, CloudIcon]
const trustBadgeLabels = ['Eigene Unterlagen', 'Lokal & Privat', 'Nur lesbar', 'Quellenangabe', 'Keine Cloud-Pflicht']

function TrustBar() {
  return (
    <div className="py-10 px-6 sm:px-10 lg:px-16">
      <div className="quellex-section-divider mb-10" />
      <div className="flex flex-wrap justify-center gap-3 sm:gap-4 quellex-reveal">
        {trustBadgeLabels.map((label, i) => {
          const Icon = trustBadgeIcons[i]
          return (
            <div
              key={i}
              className={`flex items-center gap-2.5 px-5 py-2.5 rounded-full border border-quellex-gold quellex-glass-surface quellex-card-shadow hover:quellex-card-hover-shadow hover:-translate-y-0.5 transition-all duration-300 quellex-reveal-delay-${Math.min(i, 3)}`}
            >
              <Icon className="w-4 h-4 text-quellex-gold shrink-0" />
              <span className="text-xs sm:text-sm font-medium text-primary-900 dark:text-white whitespace-nowrap">{label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── Differentiation Section ────────────────────────────────
const diffIcons = [FolderOpenIcon, EyeIcon, Squares2X2Icon, ShieldCheckIcon]
const diffCards = [
  { title: 'In Ihren Unterlagen zuhause', text: 'Quellex arbeitet direkt mit Ihren vorhandenen Akten, Ordnern und Netzlaufwerken — ohne Import oder Upload.' },
  { title: 'Klar und nachvollziehbar', text: 'Jedes Ergebnis zeigt die exakte Quellenangabe mit Seitenzahl, Absatz und Textausschnitt. Keine Blackbox.' },
  { title: 'Strukturiert im Kanzleialltag', text: 'Nahtlose Integration in bestehende Arbeitsabläufe — ohne neue Tools, ohne Umgewöhnung.' },
  { title: 'Lokal und vertraulich', text: 'Alle Daten bleiben auf Ihrer Infrastruktur. Kein Cloud-Zwang, keine externen Server, volle Kontrolle.' },
]

function DifferentiationSection() {
  return (
    <div className="py-10 px-6 sm:px-10 lg:px-16">
      <div className="max-w-3xl mx-auto text-center mb-12 quellex-reveal">
        <span className="text-xs font-semibold uppercase tracking-widest text-quellex-teal mb-4 block">
          Warum Quellex
        </span>
        <blockquote className="sm:text-2xl lg:text-3xl font-bold text-primary-900 dark:text-white leading-tight italic text-xl font-serif-quote">
          „Nicht in fremden Datenbanken oder allgemeinen Quellen suchen,{' '}
          <span className="text-quellex-gold">
            sondern dort, wo Ihre Kanzlei tatsächlich arbeitet – in Ihren eigenen Akten, Ordnern und Netzlaufwerken."
          </span>
        </blockquote>
        <p className="mt-4 text-sm text-secondary dark:text-gray-400 font-medium">— Caroline Fischerlehner</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 max-w-4xl mx-auto">
        {diffCards.map((card, i) => {
          const Icon = diffIcons[i]
          return (
            <div
              key={i}
              className={`quellex-glass-surface rounded-xl p-6 quellex-card-shadow hover:quellex-card-hover-shadow hover:-translate-y-1 transition-all duration-400 group quellex-reveal quellex-reveal-delay-${i % 3}`}
            >
              <div className="w-10 h-10 rounded-lg bg-surface-variant/40 dark:bg-gray-700/40 flex items-center justify-center mb-4 border border-quellex-gold group-hover:bg-surface-variant/60 dark:group-hover:bg-gray-700/60 transition-all duration-300">
                <Icon className="w-5 h-5 text-quellex-gold group-hover:text-quellex-gold-light transition-colors duration-300" />
              </div>
              <h3 className="text-base font-semibold text-primary-900 dark:text-white mb-2">{card.title}</h3>
              <p className="text-sm text-secondary dark:text-gray-400 leading-relaxed">{card.text}</p>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── Feature Highlights ─────────────────────────────────────
function SnippetMockup() {
  const mockResults = [
    { file: 'Urteil OGH 5Ob18/25', page: 'S. 4, Abs. 2', snippet: 'Die gesetzliche Gewährleistungsfrist beträgt bei beweglichen Sachen...' },
    { file: 'Mandant Steiner – Vertrag', page: 'S. 12, §7', snippet: 'Der Verkäufer haftet für Mängel gemäß §922 ABGB mit einer Frist von...' },
  ]

  return (
    <div className="quellex-glass-surface rounded-xl overflow-hidden quellex-card-shadow hover:quellex-card-hover-shadow hover:-translate-y-1 transition-all duration-400 w-full max-w-lg">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-surface-variant/25 dark:border-gray-700/25 bg-surface-variant/10 dark:bg-gray-800/30">
        <img src={`${ASSETS}/quellex-q-icon.png`} alt="Q" className="w-[18px] h-[18px] object-contain" />
        <span className="text-xs text-secondary dark:text-gray-400">Quellex — Quellenangabe</span>
      </div>
      <div className="p-4 space-y-3">
        <div className="flex items-center gap-2 bg-surface-variant/40 dark:bg-gray-700/40 rounded-lg px-3 py-2 border border-surface-variant/10 dark:border-gray-600/10">
          <MagnifyingGlassIcon className="w-4 h-4 text-secondary dark:text-gray-400" />
          <span className="text-sm text-secondary dark:text-gray-400">Gewährleistungsfrist Kaufvertrag</span>
        </div>
        {mockResults.map((r, i) => (
          <div key={i} className="rounded-lg border border-surface-variant/15 dark:border-gray-700/15 p-3 bg-surface-variant/5 dark:bg-gray-800/20 hover:bg-surface-variant/15 dark:hover:bg-gray-700/20 transition-colors duration-200">
            <div className="flex items-center gap-2 mb-2">
              <DocumentTextIcon className="w-3.5 h-3.5 text-quellex-gold" />
              <span className="text-xs font-medium text-primary-900 dark:text-white">{r.file}</span>
              <span className="text-xs text-quellex-teal ml-auto">{r.page}</span>
            </div>
            <p className="text-xs text-secondary dark:text-gray-400 leading-relaxed">„...{r.snippet}"</p>
            <div className="h-1.5 rounded mt-2 w-8/12" style={{ background: 'hsl(43 65% 52% / 0.2)' }} />
          </div>
        ))}
      </div>
    </div>
  )
}

function OcrMockup() {
  return (
    <div className="quellex-glass-surface rounded-xl overflow-hidden quellex-card-shadow hover:quellex-card-hover-shadow hover:-translate-y-1 transition-all duration-400 w-full max-w-lg">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-surface-variant/25 dark:border-gray-700/25 bg-surface-variant/10 dark:bg-gray-800/30">
        <img src={`${ASSETS}/quellex-q-icon.png`} alt="Q" className="w-[18px] h-[18px] object-contain" />
        <span className="text-xs text-secondary dark:text-gray-400">Quellex — OCR Verarbeitung</span>
      </div>
      <div className="p-4">
        <div className="flex gap-4">
          {/* Scanned input */}
          <div className="flex-1 rounded-lg border border-surface-variant/15 dark:border-gray-700/15 p-3 bg-surface-variant/5 dark:bg-gray-800/20">
            <div className="flex items-center gap-2 mb-3">
              <DocumentTextIcon className="w-3.5 h-3.5 text-secondary dark:text-gray-400" />
              <span className="text-xs text-secondary dark:text-gray-400">Scan_1998_Akt47.pdf</span>
            </div>
            <div className="space-y-2 opacity-50">
              <div className="h-2 bg-surface-variant/50 dark:bg-gray-600/50 rounded w-full" />
              <div className="h-2 bg-surface-variant/50 dark:bg-gray-600/50 rounded w-11/12" />
              <div className="h-2 bg-surface-variant/50 dark:bg-gray-600/50 rounded w-9/12" />
              <div className="h-2 bg-surface-variant/40 dark:bg-gray-600/40 rounded w-full" />
              <div className="h-2 bg-surface-variant/40 dark:bg-gray-600/40 rounded w-7/12" />
            </div>
          </div>
          {/* Arrow */}
          <div className="flex items-center">
            <ChevronRightIcon className="w-5 h-5 text-quellex-gold" />
          </div>
          {/* OCR output */}
          <div className="flex-1 rounded-lg border p-3 bg-surface-variant/5 dark:bg-gray-800/20" style={{ borderColor: 'hsl(43 65% 52% / 0.15)' }}>
            <div className="flex items-center gap-2 mb-3">
              <DocumentTextIcon className="w-3.5 h-3.5 text-quellex-gold" />
              <span className="text-xs text-quellex-gold">Erkannter Text</span>
            </div>
            <div className="space-y-1.5">
              <p className="text-xs text-secondary dark:text-gray-400 leading-relaxed">
                Kaufvertrag über die Liegenschaft EZ 1247, KG Innere Stadt...
              </p>
              <div className="h-1.5 rounded w-10/12" style={{ background: 'hsl(43 65% 52% / 0.2)' }} />
              <div className="h-1.5 rounded w-6/12" style={{ background: 'hsl(43 65% 52% / 0.15)' }} />
            </div>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <div className="w-2 h-2 rounded-full" style={{ background: 'hsl(180 100% 32%)' }} />
          <span className="text-xs text-quellex-teal">OCR-Qualität: 97.3% — durchsuchbar</span>
        </div>
      </div>
    </div>
  )
}

function FeatureHighlights() {
  return (
    <div className="py-10 px-6 sm:px-10 lg:px-16 space-y-14">
      <div className="quellex-section-divider" />

      {/* Snippet feature */}
      <div className="flex flex-col lg:flex-row items-center gap-10 lg:gap-16 max-w-5xl mx-auto">
        <div className="flex-1 space-y-4 text-center lg:text-left quellex-reveal">
          <span className="text-xs font-semibold uppercase tracking-widest text-quellex-teal mb-4 block">Präzise Ergebnisse</span>
          <h3 className="font-serif-display text-2xl sm:text-3xl font-bold text-primary-900 dark:text-white">
            <span className="text-quellex-gold">Quellenangabe</span>
          </h3>
          <p className="text-sm sm:text-base text-secondary dark:text-gray-400 leading-relaxed max-w-md mx-auto lg:mx-0">
            Quellex zeigt nicht nur, wo etwas gefunden wurde — sondern genau den relevanten Textabschnitt mit Seitenzahl und Absatz. So bewerten Sie Treffer sofort, ohne jedes Dokument einzeln öffnen zu müssen.
          </p>
        </div>
        <div className="flex-1 w-full flex justify-center quellex-reveal quellex-reveal-delay-2">
          <SnippetMockup />
        </div>
      </div>

      {/* OCR feature */}
      <div className="flex flex-col lg:flex-row-reverse items-center gap-10 lg:gap-16 max-w-5xl mx-auto">
        <div className="flex-1 space-y-4 text-center lg:text-left quellex-reveal">
          <span className="text-xs font-semibold uppercase tracking-widest text-quellex-teal mb-4 block">Altbestände erschließen</span>
          <h3 className="font-serif-display text-2xl sm:text-3xl font-bold text-primary-900 dark:text-white">
            OCR für <span className="text-quellex-gold">Altakten</span>
          </h3>
          <p className="text-sm sm:text-base text-secondary dark:text-gray-400 leading-relaxed max-w-md mx-auto lg:mx-0">
            Gescannte Dokumente, alte PDFs ohne Textebene? Quellex erkennt Text automatisch per OCR — und macht auch jahrzehntealte Akten durchsuchbar. Direkt auf Ihrem Server, ohne Datenabfluss.
          </p>
        </div>
        <div className="flex-1 w-full flex justify-center quellex-reveal quellex-reveal-delay-2">
          <OcrMockup />
        </div>
      </div>
    </div>
  )
}

// ─── Team Section ───────────────────────────────────────────
const teamMembers = [
  { name: 'Mirjana Čović', role: 'Technikerin & Innovationsmanagerin', photo: `${ASSETS}/team-mirjana.jpg`, bio: 'Mirjana verbindet technisches Know-how mit einem tiefen Verständnis für Nutzererfahrung. Sie gestaltet, wie Quellex in der Praxis funktioniert.', linkedin: 'https://www.linkedin.com/in/innovationslady' },
  { name: 'Emanuel Plopu', role: 'KI-Architekt', photo: `${ASSETS}/team-emanuel.jpg`, bio: 'Emanuel entwickelt die technische Architektur von Quellex. Er verantwortet die semantische Suche und die lokale Datenverarbeitung.', linkedin: 'https://www.linkedin.com/in/emanuel-plopu' },
  { name: 'Caroline Fischerlehner', role: 'Anwältin & Juristische Leitung', photo: `${ASSETS}/team-caroline.jpg`, bio: 'Caroline bringt die Perspektive der aktiven Anwältin ein. Ihr Fokus liegt auf juristischer Präzision und praktischer Nutzbarkeit.', linkedin: 'https://www.linkedin.com/in/caroline-fischerlehner-7604981a5' },
]

function TeamSection() {
  return (
    <div className="py-10 px-6 sm:px-10 lg:px-16">
      <div className="quellex-section-divider mb-16" />
      <div className="max-w-3xl mx-auto text-center mb-12 quellex-reveal">
        <span className="text-xs font-semibold uppercase tracking-widest text-quellex-teal mb-4 block">Das Team</span>
        <h2 className="font-serif-display text-2xl sm:text-3xl lg:text-4xl font-bold text-primary-900 dark:text-white leading-tight">
          Hinter Quellex stehen{' '}
          <span className="text-quellex-gold">Recht, Produkt und KI-Architektur.</span>
        </h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8 lg:gap-6 max-w-6xl mx-auto">
        {teamMembers.map((member, i) => (
          <div key={i} className={`text-center quellex-reveal quellex-reveal-delay-${i + 1}`}>
            <div className="overflow-hidden rounded-2xl quellex-card-shadow relative group bg-surface-variant dark:bg-gray-800">
              <img
                src={member.photo}
                alt={member.name}
                loading="lazy"
                className="w-full object-cover object-top aspect-[3/4] lg:aspect-[4/5] lg:max-h-[45vh] group-hover:scale-[1.04] transition-transform duration-700 ease-out"
              />
              {/* Hover bio overlay */}
              <div className="absolute inset-x-3 bottom-3 right-14 opacity-0 group-hover:opacity-100 translate-y-2 group-hover:translate-y-0 transition-all duration-400 ease-out pointer-events-none group-hover:pointer-events-auto">
                <div className="quellex-glass-surface-strong rounded-lg p-3 border quellex-card-shadow" style={{ borderColor: 'hsl(43 65% 52% / 0.15)' }}>
                  <p className="text-xs text-primary-900/80 dark:text-white/80 leading-relaxed">{member.bio}</p>
                </div>
              </div>
              {/* LinkedIn link */}
              <a
                href={member.linkedin}
                target="_blank"
                rel="noopener noreferrer"
                className="absolute bottom-[3%] right-[3%] w-10 h-10 rounded-xl bg-background/80 dark:bg-gray-900/80 backdrop-blur-sm border border-surface-variant/20 dark:border-gray-600/20 flex items-center justify-center hover:border-quellex-gold transition-all duration-300 quellex-card-shadow z-20"
              >
                <img src={`${ASSETS}/linkedin-logo.png`} alt="LinkedIn" className="w-6 h-6" />
              </a>
            </div>
            <h3 className="mt-5 text-xl font-semibold text-primary-900 dark:text-white">{member.name}</h3>
            <p className="text-xs font-medium text-quellex-gold mt-0.5">{member.role}</p>
          </div>
        ))}
      </div>

      {/* Extended Team */}
      <div className="max-w-4xl mx-auto mt-16 text-center quellex-reveal">
        <span className="text-xs font-semibold uppercase tracking-widest text-quellex-teal mb-4 block">Weiteres Team</span>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mt-6">
          {['Software Architekt', 'UI/UX Designer', 'Grafiker', 'Front-End Entwickler', 'Back-End Entwickler', 'Test/QA Engineer'].map((role, i) => (
            <div
              key={i}
              className="quellex-glass-surface-strong rounded-xl border quellex-card-shadow p-4 flex flex-col items-center gap-3"
              style={{ borderColor: 'hsl(43 65% 52% / 0.15)' }}
            >
              <div className="w-11 h-11 rounded-full flex items-center justify-center" style={{ background: 'hsl(43 65% 52% / 0.1)', border: '1px solid hsl(43 65% 52% / 0.2)' }}>
                <Squares2X2Icon className="w-5 h-5 text-quellex-gold" />
              </div>
              <p className="text-xs font-semibold text-quellex-gold leading-tight text-center">{role}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ─── Closing CTA ────────────────────────────────────────────
function ClosingCTA() {
  return (
    <div className="py-12 px-6 sm:px-10 lg:px-16">
      <div className="quellex-section-divider mb-16" />
      <div className="max-w-2xl mx-auto text-center space-y-8 quellex-reveal">
        <h2 className="font-serif-display text-2xl sm:text-3xl lg:text-4xl font-bold text-primary-900 dark:text-white leading-tight">
          Bereit, Ihr Kanzleiwissen{' '}
          <span className="text-quellex-gold">endlich nutzbar zu machen?</span>
        </h2>
        <p className="text-sm sm:text-base text-secondary dark:text-gray-400 leading-relaxed max-w-lg mx-auto">
          Vereinbaren Sie gerne eine persönliche Vorführung oder starten Sie mit einem unverbindlichen technischen Erstcheck Ihrer Infrastruktur.
        </p>
        <div className="flex justify-center pt-2">
          <LocalizedLink
            to="/login"
            className="inline-flex items-center justify-center px-10 py-3 h-12 rounded-xl bg-quellex-gold text-white font-semibold text-sm quellex-gold-glow hover:quellex-gold-glow-hover hover:-translate-y-0.5 active:translate-y-0 transition-all duration-300"
          >
            Persönliche Demo buchen
          </LocalizedLink>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
// MAIN PAGE
// ═══════════════════════════════════════════════════════════════
export default function QuellexLandingPage() {
  const { isAuthenticated } = useAuth()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [activeSection, setActiveSection] = useState('overview')

  // Scroll reveal observer
  useEffect(() => {
    const revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('revealed')
          }
        })
      },
      { threshold: 0.1, rootMargin: '0px 0px -30px 0px' }
    )

    const sectionObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && entry.target.id) {
            setActiveSection(entry.target.id)
          }
        })
      },
      { threshold: 0.3 }
    )

    const revealEls = document.querySelectorAll('.quellex-reveal')
    revealEls.forEach((el) => revealObserver.observe(el))

    const sectionEls = document.querySelectorAll('section[id]')
    sectionEls.forEach((el) => sectionObserver.observe(el))

    return () => {
      revealObserver.disconnect()
      sectionObserver.disconnect()
    }
  }, [])

  const navLinks = [
    { label: 'Funktionalität', id: 'features' },
    { label: 'Kernteam', id: 'team' },
    { label: 'Kontakt', id: 'contact' },
  ]

  const scrollToSection = (id: string) => {
    if (id === 'overview') {
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } else {
      const el = document.getElementById(id)
      if (el) {
        const top = el.offsetTop - 72
        window.scrollTo({ top, behavior: 'smooth' })
      }
    }
    setMobileNavOpen(false)
  }

  const heroCtaLink = isAuthenticated ? '/dashboard' : '/login'

  return (
    <div className="min-h-screen bg-background flex flex-col" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* ─── HEADER ─── */}
      <nav className="sticky top-0 z-50 flex items-center justify-between px-6 sm:px-10 lg:px-16 py-3 border-b bg-background/80 backdrop-blur-lg" style={{ borderColor: 'hsl(var(--quellex-border) / 0.25)' }}>
        <button onClick={() => scrollToSection('overview')} className="flex items-center">
          <QuellexLogo />
        </button>
        {/* Desktop nav */}
        <div className="hidden lg:flex items-center gap-1">
          <button
            onClick={() => scrollToSection('overview')}
            className={`p-2 rounded-md transition-all duration-300 ${activeSection === 'overview' ? 'bg-surface-variant/30 dark:bg-gray-700/30' : 'hover:bg-surface-variant/15 dark:hover:bg-gray-700/15'}`}
          >
            <HomeIcon className="w-4 h-4" style={{ color: activeSection === 'overview' ? 'hsl(43 65% 52%)' : undefined, stroke: activeSection !== 'overview' ? 'hsl(210 20% 65%)' : undefined }} />
          </button>
          {navLinks.map((link) => (
            <button
              key={link.id}
              onClick={() => scrollToSection(link.id)}
              className={`text-sm px-3 py-1.5 rounded-md transition-all duration-300 ${
                activeSection === link.id
                  ? 'text-quellex-gold bg-surface-variant/30 dark:bg-gray-700/30 font-medium'
                  : 'text-secondary dark:text-gray-400 hover:text-primary-900 dark:hover:text-white hover:bg-surface-variant/15 dark:hover:bg-gray-700/15'
              }`}
            >
              {link.label}
            </button>
          ))}
        </div>
        {/* Right controls */}
        <div className="flex items-center gap-1">
          <LanguageSwitcher />
          <ThemeSwitcher />
          <LocalizedLink
            to="/login"
            className="hidden lg:inline-flex items-center justify-center px-4 py-1.5 rounded-lg bg-quellex-gold text-white font-semibold text-xs quellex-gold-glow hover:quellex-gold-glow-hover hover:-translate-y-0.5 transition-all duration-300"
          >
            Demo anfordern
          </LocalizedLink>
          {/* Mobile hamburger */}
          <button
            className="lg:hidden p-2 rounded-md hover:bg-surface-variant/15 dark:hover:bg-gray-700/15"
            onClick={() => setMobileNavOpen(!mobileNavOpen)}
          >
            {mobileNavOpen ? <XMarkIcon className="w-5 h-5" /> : <Bars3Icon className="w-5 h-5" />}
          </button>
        </div>
      </nav>

      {/* Mobile nav drawer */}
      {mobileNavOpen && (
        <>
          <div className="fixed inset-0 z-40 bg-black/30" onClick={() => setMobileNavOpen(false)} />
          <div className="fixed right-0 top-0 h-full w-[196px] z-50 bg-background/85 backdrop-blur-md border-l p-6 pt-16 flex flex-col gap-4" style={{ borderColor: 'hsl(var(--quellex-border) / 0.15)' }}>
            <button onClick={() => setMobileNavOpen(false)} className="absolute top-4 right-4">
              <XMarkIcon className="w-5 h-5" />
            </button>
            <button onClick={() => scrollToSection('overview')} className="text-sm text-left text-secondary dark:text-gray-400 hover:text-quellex-gold py-2">
              Overview
            </button>
            {navLinks.map((link) => (
              <button
                key={link.id}
                onClick={() => scrollToSection(link.id)}
                className="text-sm text-left text-secondary dark:text-gray-400 hover:text-quellex-gold py-2"
              >
                {link.label}
              </button>
            ))}
            <LocalizedLink
              to="/login"
              className="mt-4 inline-flex items-center justify-center px-4 py-2 rounded-lg bg-quellex-gold text-white font-semibold text-xs"
            >
              Demo anfordern
            </LocalizedLink>
          </div>
        </>
      )}

      {/* ─── MAIN CONTENT ─── */}
      <main className="flex-1">
        {/* Hero */}
        <section id="overview" className="min-h-[85vh] flex items-center relative overflow-hidden">
          <div className="w-full flex flex-col lg:flex-row items-center justify-center gap-8 lg:gap-12 px-6 sm:px-10 lg:px-16 py-16 relative z-10 max-w-7xl mx-auto">
            {/* Left: text content */}
            <div className="flex-1 max-w-xl space-y-6 quellex-animate-slide-up text-center lg:text-left">
              <div className="flex justify-center mb-4">
                <QuellexLogo size="large" />
              </div>
              <h1>
                <img
                  src={`${ASSETS}/hero-heading.png`}
                  alt="Ihr Kanzleiwissen. Schnell gefunden."
                  className="w-full max-w-xl hidden dark:block mix-blend-screen"
                />
                <img
                  src={`${ASSETS}/hero-heading-light.png`}
                  alt="Ihr Kanzleiwissen. Schnell gefunden."
                  className="w-full max-w-xl block dark:hidden mix-blend-multiply opacity-85"
                />
              </h1>
              <p className="text-base sm:text-lg text-secondary dark:text-gray-400 leading-relaxed max-w-lg mx-auto lg:mx-0 text-center whitespace-pre-wrap">
                Quellex durchsucht Ihre eigenen Akten, Ordner und Netzlaufwerke — sorgfältig, lokal und mit voller Nachvollziehbarkeit.
              </p>
              <div className="flex flex-col sm:flex-row gap-3 justify-center">
                <LocalizedLink
                  to={heroCtaLink}
                  className="inline-flex items-center justify-center px-8 py-3 rounded-xl bg-quellex-gold text-white font-semibold text-sm quellex-gold-glow hover:quellex-gold-glow-hover hover:-translate-y-0.5 active:translate-y-0 transition-all duration-300"
                >
                  Persönliche Demo buchen
                </LocalizedLink>
              </div>
              {/* Trust badges */}
              <div className="flex items-center gap-6 pt-4 justify-center lg:justify-start">
                {[
                  { label: '100% lokal', info: 'Alle Daten bleiben auf Ihrer Infrastruktur' },
                  { label: 'DSGVO-konform', info: 'Vollständig datenschutzkonform nach EU-Recht' },
                  { label: 'Made in Austria', info: 'Entwickelt und gehostet in Österreich' },
                ].map((item) => (
                  <div key={item.label} className="group relative flex items-center gap-2 cursor-default">
                    <div className="w-2 h-2 rounded-full" style={{ background: 'hsl(180 100% 32%)' }} />
                    <span className="text-xs text-secondary dark:text-gray-400">{item.label}</span>
                    <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 rounded-lg quellex-glass-surface text-xs text-primary-900 dark:text-white whitespace-nowrap opacity-0 pointer-events-none group-hover:opacity-100 transition-opacity duration-200 quellex-card-shadow">
                      {item.info}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Lawyer seal */}
            <img
              src={`${ASSETS}/lawyer-seal.png`}
              alt="Anwaltlich geprüft – Österreich"
              className="absolute left-1/2 top-[60%] -translate-x-1/2 -translate-y-1/2 w-28 h-28 lg:w-36 lg:h-36 z-10 drop-shadow-lg opacity-70 hidden lg:block rotate-[-8deg] pointer-events-none"
              loading="lazy"
            />

            {/* Right: Mock Interface */}
            <div className="flex-1 flex justify-center items-center max-w-lg w-full quellex-animate-fade-in" style={{ animationDelay: '0.2s', opacity: 0 }}>
              <MockInterface />
            </div>
          </div>
        </section>

        {/* Trust Bar */}
        <div className="max-w-7xl mx-auto">
          <TrustBar />
        </div>

        {/* Features */}
        <section id="features" className="max-w-7xl mx-auto">
          <DifferentiationSection />
          <FeatureHighlights />
        </section>

        {/* Team */}
        <section id="team" className="max-w-7xl mx-auto">
          <TeamSection />
        </section>

        {/* Closing CTA */}
        <section id="contact" className="max-w-7xl mx-auto">
          <ClosingCTA />
        </section>
      </main>

      {/* ─── FOOTER ─── */}
      <footer className="px-6 sm:px-10 lg:px-16 py-6 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between text-center sm:text-left" style={{ borderTop: '1px solid hsl(var(--quellex-border) / 0.15)', background: 'hsl(var(--quellex-navy-surface) / 0.05)' }}>
        <span className="text-xs text-secondary dark:text-gray-400">© 2026 Quellex · Wien, Österreich</span>
        <div className="flex items-center justify-center sm:justify-end gap-4">
          <span className="text-xs text-secondary dark:text-gray-400 hidden sm:block">Für österreichische Rechtsanwaltskanzleien</span>
        </div>
      </footer>
    </div>
  )
}

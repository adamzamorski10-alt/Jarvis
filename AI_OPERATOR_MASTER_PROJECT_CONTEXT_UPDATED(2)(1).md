# AI OPERATOR — MASTER PROJECT CONTEXT
## Wersja 3.0 — aktualny źródłowy kontekst projektu

**Status:** aktywny dokument źródłowy prawdy dla architektury, bezpieczeństwa, roadmapy i aktualnego stanu projektu  
**Data aktualizacji:** 2026-09-14  
**Wersja dokumentu:** 3.0  
**Poprzednia wersja:** 2.0 z 2026-09-06

> Ten dokument zastępuje wcześniejszy roadmap oparty o stare numery etapów. Zachowuje ważne decyzje architektoniczne, ale aktualizuje rzeczywisty stan implementacji. Nie wolno traktować historycznych opisów jako aktualnego planu wykonawczego.

---

# 1. CZYM JEST AI OPERATOR?

AI Operator to prywatny, lokalny, desktopowy system osobistej inteligencji dla Windows. Docelowo ma być centralnym systemem zarządzającym cyfrowym środowiskiem użytkownika.

Nie jest to zwykły chatbot. Docelowo system ma obsługiwać m.in.:

- projekty programistyczne i autonomiczne tworzenie oprogramowania;
- pliki, dokumenty i foldery;
- pamięć i historię;
- przeglądanie WWW i research;
- e-mail, kalendarz i komunikatory;
- automatyzacje, scheduler, events i notifications;
- monitoring i watchlists;
- komputer i urządzenia użytkownika;
- telefon i zdalne sterowanie;
- wiele modeli AI i wielu providerów;
- głos jako jeden z głównych interfejsów;
- aplikacyjne skills/capabilities z osobnymi uprawnieniami;
- pracę autonomiczną podczas nieobecności użytkownika;
- propozycje samodoskonalenia, ale tylko w ramach kontrolowanego procesu.

Docelowa zasada:

> **MODEL PROPOSES. APPLICATION DECIDES. AUTHORIZED APPLICATION SERVICES EXECUTE.**

Model AI nie otrzymuje nieograniczonego dostępu do systemu. Model proponuje decyzje i działania. Aplikacja waliduje je, stosuje polityki, uprawnienia i approval, a następnie autoryzowane serwisy wykonują akcję.

Projekt ma preferować bezpłatne i lokalne komponenty, szczególnie Ollama, oraz darmowe limity usług tam, gdzie jest to praktyczne.

---

# 2. NAJWAŻNIEJSZY CEL

Najbliższym praktycznym celem nie jest jeszcze „pełny AI Operator”. Najpierw ma powstać **Autonomous Coding MVP** — pierwsza rzeczywiście używalna wersja systemu.

Użytkownik powinien móc przekazać np.:

> „Zrealizuj ten plan projektu w wskazanym folderze. Zaplanuj pracę, wykonaj zadania, napisz kod, testuj, naprawiaj błędy i doprowadź projekt do zweryfikowanego stanu.”

Docelowy przepływ dla Autonomous Coding MVP:

```text
USER REQUEST / PROJECT PLAN
        ↓
AI MANAGER
        ↓
STRUCTURED MANAGER DECISION
        ↓
POLICY + CAPABILITY + APPROVAL
        ↓
PLAN MANAGER
        ↓
PERSISTED PROJECT PLAN / TASK DAG
        ↓
CONTROLLED EXECUTION
        ↓
DEVELOPMENT AGENT
        ↓
ISOLATED WORKSPACE + GIT
        ↓
CONTROLLED DEVELOPMENT TOOLS
        ↓
VERIFICATION GATE
        ├── PASS → COMMIT
        └── FAIL → REPAIR → RETEST
        ↓
FINAL REPORT / HISTORY
```

Najważniejsza własność: stan projektu należy do AI Operatora, a nie do pamięci pojedynczej sesji modelu lub agenta.

---

# 3. ARCHITEKTURA DOCZELOWA

Docelowy system jest modułowy:

```text
                    ┌─────────────────────┐
                    │      USER / UI      │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │     AI MANAGER      │
                    │ decision / planning │
                    └──────────┬──────────┘
                               ↓
              ┌────────────────┴────────────────┐
              ↓                                 ↓
       Policy / Capability                Approval Service
              ↓                                 ↓
              └────────────────┬────────────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Execution Services  │
                    └──────────┬──────────┘
                               ↓
             ┌─────────────────┴─────────────────┐
             ↓                                   ↓
      Autonomous Dev Core                 Future App Skills
             ↓                                   ↓
   Development Agent                      Email / Browser /
   Workspace / Git                        Files / Calendar /
   Verification / Recovery                Computer / Mobile
```

Zasady graniczne:

- AI Manager nie wykonuje narzędzi bezpośrednio.
- Model nie kontroluje task_id, run_id, execution_id, workspace, Git, verification ani execution fencing.
- PermissionEngine jest jedynym autorytetem dla permission decyzji narzędziowych.
- ToolExecutionService jest kontrolowaną bramą wykonywania narzędzi.
- VerificationGate niezależnie sprawdza rezultat.
- WorkspaceManager/Git kontroluje izolację i checkpointy.
- ApprovalService obsługuje jawne zgody użytkownika.
- DENY jest ostateczne i nie może zostać nadpisane przez approval.
- AUTO MERGE do main pozostaje OFF.

---

# 4. AKTUALNY ZWERYFIKOWANY STAN — 2026-09-14

## 4.1 Security Foundation
**STATUS: CLOSED**

Zrealizowane fundamenty obejmują:

- Tool Registry;
- Permission Engine;
- grants i approval primitives;
- Audit Log;
- BaseTool / ToolImplementationRegistry / ToolExecutor;
- Tool Execution security boundary;
- AI/provider normalization;
- provider tool calling;
- Approval Security Boundary;
- end-to-end security integration.

Obowiązująca granica:

```text
AI / Manager
    ↓
Capability / Tool contract
    ↓
Policy / Permission / Approval
    ↓
ToolExecutionService
    ↓
ToolExecutor
    ↓
Audit
```

Nie wolno tworzyć bocznej ścieżki omijającej te mechanizmy.

## 4.2 Autonomous Development Core
**STATUS: CLOSED — v1**

Zrealizowane i zweryfikowane:

- durable development tasks;
- task state machine;
- atomic task claims;
- concurrency safety;
- bounded attempts;
- retry;
- isolated Git worktrees;
- primary worktree protection;
- verification before commit;
- crash/timeout recovery;
- execution fencing;
- workspace ownership;
- autonomous Run Controller;
- controlled development tools;
- plan/task DAG;
- REST control plane;
- background execution manager;
- autonomous plan execution;
- bounded autonomous repair loop;
- deterministic multi-task DAG execution;
- cancellation, recovery i concurrency hardening.

**Stage 8.16 zamknął Autonomous Development Core v1.**

Ostatni raport Stage 8.16 potwierdził m.in. pełną regresję 1048 passed / 2 skipped oraz brak nierozwiązanych CRITICAL/HIGH/MEDIUM w finalnym hostile gate.

## 4.3 Real local development E2E
**STATUS: CLOSED / VERIFIED WITH LIMITATION**

Realny lokalny E2E z Ollama został wykonany na `qwen2.5-coder:7b`.

Zweryfikowano pełną ścieżkę:

```text
real Ollama
 → Coding Adapter
 → DevelopmentAgent
 → WorkspaceWriter
 → VerificationGate
 → Git commit
```

Model wykonał rzeczywistą zmianę kodu i testów, VerificationGate potwierdził wynik, a system utworzył commit.

Ograniczenie: 7B może czasami nie przestrzegać tool-calling contract i zwrócić zwykły tekst. Adapter ma wtedy zachować się fail-closed. Jest to obecnie ograniczenie modelu, nie powód do łamania architektury bezpieczeństwa.

## 4.4 AI Manager
**STATUS: FOUNDATION + REAL PROVIDERS CLOSED**

Zrealizowano:

- frozen AI Manager contract;
- UserIntent;
- ManagerDecision;
- ActionProposal;
- ManagerResult;
- decision outcomes;
- risk levels;
- capability identifiers;
- policy model;
- bounded/strict validation;
- provider-neutral AI Manager;
- Ollama provider;
- OpenRouter provider;
- structured manager decision pipeline;
- parser + normalizer boundary;
- controlled transient retries;
- AI Manager → PlanManager bridge;
- policy/capability decision gate;
- approval foundation.

Obowiązuje:

```text
UserIntent
  ↓
AIManager
  ↓
AIManagerProvider
  ↓
raw structured output
  ↓
parser
  ↓
normalizer
  ↓
ManagerDecision
```

Realne adaptery korzystają z istniejących providerów Ollama/OpenRouter i nie tworzą równoległych klientów HTTP.

## 4.5 Stage 9.3 — AI Manager → PlanManager Bridge
**STATUS: CLOSED**

Commit: `aa7d807cff89df1f6336e98dcc1c0b80f80ee9b8`

Bridge:

```text
UserIntent
 → AIManager
 → ManagerResult / ManagerDecision
 → policy eligibility
 → UserPlan
 → PlanManager
```

Bridge nie wykonuje tasków. PlanManager i TaskRepository pozostają właścicielami planowania i persistence.

## 4.6 Stage 9.4 — AI Manager Policy & Capability Decision Gate
**STATUS: CLOSED**

Etap został zamknięty.

Aktualna zasada architektoniczna:

- capabilities są definiowane przez aplikację;
- model nie definiuje ich znaczenia ani uprawnień;
- nieznane capability jest odrzucane fail-closed;
- application-owned risk/policy mają pierwszeństwo przed deklaracją modelu;
- approval jest odrębny od capability i permission;
- decyzja może być odrzucona nawet wtedy, gdy model chce ją wykonać;
- gate nie daje modelowi execution authority.

## 4.7 Stage 9.5 — Approval Foundation
**STATUS: CLOSED**

Etap został zamknięty.

Approval ma być traktowany jako jawna decyzja aplikacji/użytkownika, a nie jako pole kontrolowane przez model.

Kluczowe zasady dalszego rozwoju:

- approval musi być związany z konkretnym zakresem działania;
- approval nie może nadpisać DENY;
- approval nie jest permission;
- approval nie jest capability;
- approval powinien być zużywany atomowo tam, gdzie jest jednorazowy;
- nie wolno pozwolić modelowi samemu „zatwierdzić” własnej akcji.

## 4.8 Stage 9.6 — AI Manager → Controlled Execution
**STATUS: ACTIVE / NOT CLOSED**

To jest aktualny etap.

Cel: po raz pierwszy bezpiecznie połączyć warstwę AI Managera z istniejącym Autonomous Development Core.

Zakres jest celowo wąski: tylko istniejąca ścieżka wykonywania autonomicznego developmentu. Nie budujemy jeszcze generic executor.

Wymagane zasady:

- application-owned execution authorization;
- dokładne powiązanie approval z zakresem wykonania;
- DENY jest nieodwracalne;
- jednorazowe approval consumption jest atomowe;
- replay/concurrency protection;
- execution dopiero po ponownym sprawdzeniu policy/capability/target;
- wykonywany jest wyłącznie istniejący persisted plan;
- existing execution core pozostaje właścicielem lifecycle;
- model nie kontroluje execution/task/run/workspace/Git/verification IDs;
- cancellation, timeout i recovery pozostają w istniejącym core;
- wszystkie istotne decyzje są audytowalne;
- nie tworzyć endpointu typu `execute-anything`.

Stage 9.6 nie powinien rozszerzać zakresu na browser, email, messaging, payments, computer use ani arbitrary shell.

---

# 5. HISTORYCZNA MAPA STAGE — CO ZOSTAŁO ZROBIONE

## Stage 5 — Security Foundation
**CLOSED**

Tool Registry, Permission Engine, grants, Audit Log, ToolExecutor, Approval boundary i security integration.

## Stage 6 — Autonomous Development Foundation
**CLOSED**

Stage 6.1–6.8 zostały ostatecznie domknięte. Historycznie Stage 6.8 wymagał dodatkowego hostile remediation dotyczącego cancel/abort/timeout/workspace ownership. Finalny rezultat został później uzyskany w ramach hardeningu i kolejnych etapów core.

## Stage 7 — Verification Engine
**CLOSED**

Verification obejmuje m.in. pytest, TypeScript, frontend build, lint i fail-closed VerificationGate. Finalna regresja Stage 7: 649 backend tests oraz 48 frontend tests; dodatkowo powtarzalność 10×.

## Stage 8 — Autonomous Development Core
**CLOSED**

Stage 8 obejmował kolejno:

- architecture/plan contract;
- idempotent task DAG persistence;
- provider-neutral PlanManager;
- OpenRouter planning provider;
- PlanService;
- DevelopmentAgent contract;
- CodingOperation protocol;
- WorkspaceWriter;
- Ollama/OpenRouter coding adapters;
- hostile coding/provider audit;
- provider integration;
- real Ollama E2E;
- controlled execution capabilities;
- AI Manager-independent planning/execution integration;
- autonomous repair;
- autonomous plan loop;
- REST control plane;
- final autonomous core hardening.

**Stage 8.16 = Autonomous Development Core v1 CLOSED.**

---

# 6. PIERWSZY PRAKTYCZNY MILESTONE — AUTONOMOUS CODING MVP

**STATUS: IN PROGRESS**

To jest pierwszy poziom, który ma być realnie używalnym programem.

## MVP ma umożliwiać

1. przyjęcie requestu/planu użytkownika;
2. AI Manager podejmuje strukturalną decyzję;
3. policy/capability gate sprawdza możliwość działania;
4. approval jest wymagany tam, gdzie wynika to z polityki;
5. plan jest tworzony i zapisany;
6. taski tworzą DAG;
7. execution jest kontrolowane przez Autonomous Development Core;
8. Development Agent pracuje w izolowanym workspace;
9. agent może wykonywać tylko dozwolone działania developmentowe;
10. VerificationGate sprawdza wynik;
11. failure uruchamia bounded repair;
12. retry/recovery są kontrolowane;
13. Git checkpoint/commit następuje dopiero po wymaganej weryfikacji;
14. użytkownik otrzymuje końcowy wynik i historię;
15. użytkownik może zatrzymać, anulować lub przejąć kontrolę.

## Co jeszcze jest potrzebne do praktycznego MVP

### A. Stage 9.6
Połączyć AI Manager z controlled execution.

### B. Kilo Code Integration
Kilo ma stać się kolejnym providerem DevelopmentAgent, bez uzależniania core od Kilo.

### C. Kilo session/resume/recovery
System musi sam radzić sobie z:

- empty response;
- context limit;
- compaction;
- rate limit;
- provider outage;
- timeout;
- crash;
- częściowym wykonaniem;
- potrzebą nowej sesji.

### D. Real project E2E
Nie tylko mały test demonstracyjny, ale rzeczywisty projekt z wieloma taskami, zależnościami, repair i recovery.

### E. Usable control UI
Minimalny interfejs do uruchamiania projektów, obserwowania tasków, approval i wyników. Nie trzeba jeszcze budować pełnego finalnego UI.

Po tych elementach można uznać **Autonomous Coding MVP** za praktycznie gotowy do codziennego użycia w kontrolowanym zakresie.

---

# 7. KILO CODE — ROLA I INTEGRACJA

Kilo Code ma być Development Agentem, a nie nadrzędnym kontrolerem systemu.

Docelowo:

```text
DevelopmentAgent
 ├── LocalCodingProvider / Ollama
 ├── OpenRouterCodingProvider
 ├── KiloCodeAdapter
 └── FutureAgentAdapters
```

Kilo nie powinno posiadać:

- własnego źródła prawdy o stanie projektu;
- prawa do zmiany security policy;
- prawa do samodzielnego zatwierdzania approval;
- prawa do omijania ToolExecutionService;
- prawa do omijania VerificationGate;
- prawa do samodzielnego merge do main.

Kilo może utracić kontekst. AI Operator nie może utracić stanu projektu.

Źródła prawdy:

- Task DB;
- persisted plan;
- execution state;
- workspace state;
- Git;
- verification results;
- audit/history;
- durable reports.

---

# 8. OBSŁUGA BŁĘDÓW I NIEZAWODNOŚĆ

Każdy provider/agent musi mieć kontrolowaną klasyfikację błędów, np.:

```text
EMPTY_RESPONSE
RATE_LIMITED
QUOTA_EXCEEDED
CONTEXT_LIMIT
COMPACTION_ERROR
PROVIDER_UNAVAILABLE
INVALID_REQUEST
AUTH_ERROR
TOOL_ERROR
TIMEOUT
PROCESS_CRASH
UNKNOWN_ERROR
```

Dla każdego błędu system określa:

- retryable / non-retryable;
- backoff;
- max retries;
- możliwość nowej sesji;
- możliwość resume;
- potrzebę repair;
- możliwość PAUSED/FAILED.

Nigdy nie wolno implementować nieskończonego retry.

Każdy autonomiczny run musi być ograniczony m.in. przez:

- max attempts;
- max repair attempts;
- max recovery attempts;
- max runtime;
- max provider retries;
- max empty responses;
- max session restarts;
- opcjonalny limit kosztu/tokenów dla zewnętrznych API.

---

# 9. TASK / EXECUTION STATE

Obowiązujący kontrakt task state pozostaje kontrolowany przez istniejącą TaskStateMachine.

Stany:

```text
PENDING
RUNNING
VERIFYING
PASSED
REPAIR_REQUIRED
COMMITTED
PAUSED
FAILED
CANCELLED
ABORTED
```

Nie dodawać `RECOVERING` bez osobnej decyzji architektonicznej.

Każda zmiana stanu musi przechodzić przez właściwą warstwę state machine / lifecycle. Model nie może zmieniać stanu bezpośrednio.

Execution fencing pozostaje krytycznym mechanizmem bezpieczeństwa.

---

# 10. WORKSPACE I GIT

Każde autonomiczne wykonanie musi mieć jasno określoną przestrzeń roboczą.

```text
project
 ├── execution A → workspace A
 ├── execution B → workspace B
 └── execution C → workspace C
```

Workspace nie może zostać usunięty, jeśli istnieje możliwość aktywnego execution.

Primary worktree użytkownika pozostaje chroniony.

Obowiązuje:

- dedicated Git worktree;
- kontrolowany staged file set;
- weryfikacja commit SHA;
- brak ślepego `git add .` / `git add -A` jako polityki bezpieczeństwa;
- brak `git commit --no-verify` jako obejścia kontroli;
- AUTO MERGE = OFF;
- brak automatycznego push, jeśli polityka tego nie zezwala.

---

# 11. VERIFICATION

Kod nie jest gotowy dlatego, że agent twierdzi „done”.

Kod jest gotowy dopiero po wymaganych checks.

Verification pozostaje niezależny od modelu i Development Agenta.

Możliwe checks obejmują:

- unit tests;
- integration tests;
- pytest;
- TypeScript;
- build;
- lint;
- runtime smoke;
- security checks;
- repository consistency;
- diff inspection;
- inne wymagania konkretnego projektu.

Verification Gate jest warunkiem przed commit.

---

# 12. AI MANAGER — AKTUALNA ROLA

AI Manager jest warstwą decyzyjną, nie warstwą wykonawczą.

Jego odpowiedzialności:

- analiza requestu;
- normalizacja celu;
- rozpoznanie wymaganych capabilities;
- określenie proponowanego działania;
- planowanie;
- analiza błędów;
- propozycje repair;
- interpretacja wyników;
- planowanie kolejnych kroków.

AI Manager nie może bezpośrednio:

- uruchamiać shell/subprocess;
- modyfikować filesystemu poza autoryzowanym serwisem;
- wykonywać Git;
- zmieniać task/run state;
- omijać PermissionEngine;
- omijać ApprovalService;
- omijać VerificationGate;
- samodzielnie tworzyć arbitrary background jobs;
- podejmować nieautoryzowanych działań zewnętrznych.

Aktualne provider options:

- Ollama;
- OpenRouter;
- przyszłe providery przez adaptery.

Nie zakładamy, że jeden konkretny model będzie „mózgiem na zawsze”.

---

# 13. APPROVAL / PERMISSIONS / CAPABILITIES

To są trzy różne pojęcia:

```text
CAPABILITY
  = co aplikacja potrafi

PERMISSION / POLICY
  = czy dane działanie jest dozwolone

APPROVAL
  = czy użytkownik jawnie zgodził się na konkretny wymagający zakres
```

Model może zaproponować capability, ale aplikacja definiuje, czy capability istnieje i co oznacza.

Model może podać risk level, ale application policy może go podnieść lub całkowicie odrzucić działanie.

Approval nie może zmienić DENY w ALLOW.

Jednorazowe approval musi być zużywane atomowo i nie może zostać wykorzystane ponownie przez replay/concurrency.

---

# 14. PRZYSZŁA ROADMAPA — PO AUTONOMOUS CODING MVP

Kolejność jest zarządzana przez AI Operator architecture/strategy, a nie przez stare numery etapów z wersji 2.0.

## Milestone 1 — Autonomous Coding MVP
**STATUS: IN PROGRESS**

9.6 → Kilo integration → Kilo session/recovery → real project E2E → minimal usable UI.

## Milestone 2 — Durable Memory & Development History
**STATUS: NEXT / PLANNED**

- project memory;
- development history;
- decisions;
- bugs;
- architecture notes;
- task/run history;
- reports;
- useful user preferences;
- searchable activity history.

Sekrety nigdy nie są częścią zwykłej pamięci modelu.

## Milestone 3 — File Agent
**STATUS: FUTURE**

Bezpieczne, policy-controlled:

- list;
- search;
- metadata;
- read;
- create;
- copy;
- move;
- rename;
- recycle-bin delete.

Permanent delete nie jest domyślnym mechanizmem.

## Milestone 4 — Browser / Web Research
**STATUS: FUTURE**

Najpierw official APIs i kontrolowane web capabilities. Computer Use nie powinien być używany jako domyślny zamiennik API.

## Milestone 5 — Email / Calendar / Messaging
**STATUS: FUTURE**

Każda integracja ma mieć osobny capability/permission/approval policy.

Wysyłanie wiadomości i podobne akcje mają wyższy poziom ryzyka niż samo odczytywanie danych.

## Milestone 6 — Automations / Scheduler / Events / Notifications
**STATUS: FUTURE**

System ma reagować:

```text
reactive → contextual → proactive → autonomous
```

ale nigdy:

```text
reactive → uncontrolled
```

Automatyzacje muszą mieć jawny zakres, harmonogram, warunki, limity i audit.

## Milestone 7 — Computer Use
**STATUS: FUTURE**

Pełna kontrola komputera dopiero po stabilizacji permission model, approvals, audit i recovery. Preferowane są API i wyspecjalizowane capabilities zamiast generic GUI automation, gdy jest taka możliwość.

## Milestone 8 — Mobile / Remote Control
**STATUS: FUTURE**

Telefon ma być panelem sterowania, approval, monitoringu i powiadomień, a później również kontrolowanym urządzeniem wykonawczym.

Nie wdrażać przed stabilnym lokalnym core.

## Milestone 9 — Multi-Model Router / Provider Registry
**STATUS: FUTURE / PARTIALLY PREPARED**

System ma dobierać model do zadania według:

- jakości;
- kosztu;
- lokalności;
- limitów;
- kontekstu;
- tool calling;
- szybkości;
- dostępności.

## Milestone 10 — Proactive Monitoring / Watchlists
**STATUS: FUTURE**

Monitorowanie tylko źródeł, do których użytkownik jawnie przyznał dostęp.

## Milestone 11 — Agent Teams
**STATUS: FUTURE**

Wiele wyspecjalizowanych agentów współpracujących pod kontrolą centralnego Operatora.

## Milestone 12 — Self-Improvement Proposals
**STATUS: FUTURE**

System może proponować usprawnienia własnego działania, ale nie może samodzielnie zmieniać krytycznych zabezpieczeń bez kontrolowanego procesu weryfikacji.

---

# 15. FINANSE — SPECJALNA ZASADA

Początkowo system finansowy może zarządzać danymi finansowymi w aplikacji użytkownika, ale **nie wykonuje rzeczywistych przelewów ani płatności**.

Realne transakcje finansowe są przyszłym, osobnym i wysokiego ryzyka obszarem wymagającym dodatkowej architektury, approvals i polityk.

---

# 16. AUTONOMIA

Docelowe poziomy:

### ASSISTED
AI analizuje i proponuje. Użytkownik wykonuje lub zatwierdza większość działań.

### SMART
Niskie ryzyko może być wykonywane automatycznie, wyższe wymaga zgody.

### AUTONOMOUS
Większość wcześniej dozwolonych działań może być wykonywana bez każdorazowego pytania, w granicach polityk.

### FULL AUTONOMY WITHIN POLICY
System może pracować podczas nieobecności użytkownika, ale nadal obowiązują permissions, approvals, budgets, audit, execution fencing i fail-closed security.

„Pełna autonomia” nigdy nie oznacza braku zasad.

---

# 17. POZIOMY RYZYKA

Orientacyjnie:

### GREEN
- analiza;
- odczyt dozwolonych danych;
- raporty;
- praca w izolowanym workspace.

### YELLOW
- modyfikacja plików;
- development tools;
- niekrytyczne zmiany konfiguracji.

### ORANGE
- instalacje;
- push;
- wysyłanie wiadomości;
- działania o większym wpływie.

### RED
- trwałe usuwanie;
- zmiany bezpieczeństwa;
- eksport sekretów;
- realne transakcje finansowe;
- nieznany kod poza zatwierdzonym środowiskiem.

RED domyślnie wymaga bezpośredniej zgody, chyba że przyszła, osobno zweryfikowana polityka stanowi inaczej.

---

# 18. WIELE PROJEKTÓW I RÓWNOLEGŁOŚĆ

Docelowo:

```text
AI Operator
 ├── Project A
 │    ├── Task A1
 │    └── Task A2
 ├── Project B
 │    ├── Task B1
 │    └── Task B2
 └── Project C
      └── Task C1
```

Każdy execution musi posiadać niezależną tożsamość, workspace, state, verification i historię.

Równoległość musi respektować:

- CPU;
- RAM;
- VRAM;
- procesy;
- provider limits;
- user policy;
- workspace ownership;
- execution fencing.

Nie uruchamiać wielu ciężkich lokalnych modeli tylko dlatego, że istnieje wiele zadań.

---

# 19. INTERFEJS DOCZELOWY

Docelowy Control Center może zawierać:

```text
Dashboard
Assistant / Chat
Voice
Tasks
Projects
Agents
Files
Browser / Research
Automations
Approvals
Notifications
Memory
Activity / History
AI Models
AI Providers
Permissions
Settings
```

Dashboard powinien pokazywać m.in.:

- aktywne projekty;
- aktywne execution;
- task status;
- agent status;
- verification;
- błędy;
- recovery;
- approval requests;
- wykorzystanie zasobów;
- tryb autonomii.

Minimalny UI potrzebny do Autonomous Coding MVP nie musi jeszcze zawierać wszystkich tych zakładek.

---

# 20. STANDARD TESTÓW I WERYFIKACJI

Każdy istotny etap musi być sprawdzony odpowiednio do ryzyka.

Wymagane są, gdy mają zastosowanie:

- unit tests;
- integration tests;
- concurrency tests;
- failure injection;
- repeated runs;
- full regression;
- hostile security audit;
- real E2E dla krytycznych integracji.

Ważna zasada:

> Zielone testy nie wystarczają, jeśli hostile audit wykazuje lukę architektoniczną.

Finalna bramka krytycznego etapu powinna potwierdzić:

- brak CRITICAL;
- brak HIGH;
- brak nierozwiązanych MEDIUM;
- zgodność z invariants;
- pełną regresję;
- odpowiedni E2E;
- clean Git state poza świadomie zaakceptowanymi plikami dokumentacyjnymi.

Pełna regresja nie powinna być uruchamiana po każdej małej zmianie, jeśli nie jest to potrzebne. Ma być finalną bramką etapu, zgodnie z zasadą staged development.

---

# 21. ZASADY DLA AI PROGRAMUJĄCEGO

1. Najpierw analizuj istniejący kod.
2. Nie przepisuj modułów bez potrzeby.
3. Nie usuwaj funkcji bez uzasadnienia.
4. Duże zmiany dziel na etapy.
5. Implementuj tylko zakres bieżącego etapu.
6. Po implementacji wykonaj odpowiednie testy.
7. Najpierw LOW reasoning dla prostych zmian i testów.
8. MEDIUM dla trudnej implementacji/debugowania.
9. HIGH tylko dla architektury, trudnego security i końcowego hostile audit.
10. Nie łącz discovery, implementacji, wielkiej regresji i hostile audit w jeden mały prompt bez potrzeby.
11. Nie deklaruj sukcesu bez dowodów.
12. Nie twórz atrap zamiast implementacji.
13. Nie dawaj modelowi nieograniczonego shell access.
14. Preferuj official API nad GUI automation.
15. Preferuj wyspecjalizowane narzędzia nad generic shell.
16. Wszystkie skuteczne akcje muszą być audytowalne.
17. Sekrety nie trafiają do kodu, logów, raportów ani promptów bez potrzeby.
18. Nie omijaj ToolExecutionService.
19. Nie omijaj PermissionEngine.
20. Nie omijaj ApprovalService.
21. Nie omijaj TaskStateMachine.
22. Nie omijaj VerificationGate.
23. Nie usuwaj workspace aktywnego execution.
24. Timeout nie jest dowodem zakończenia execution.
25. Empty response nie jest sukcesem.
26. Nie zakładaj, że agent pamięta poprzednią sesję.
27. Stan projektu musi być durable.
28. AUTO MERGE do main pozostaje OFF.
29. Nie wykonuj realnych płatności/przelewów w początkowym systemie.
30. Każde krytyczne założenie potwierdzaj testem, audytem lub rzeczywistym E2E.
31. Proaktywnie zgłaszaj, jeśli planowana funkcja jest niepotrzebnie skomplikowana lub przedwczesna.
32. Preferuj prostsze rozwiązanie, jeśli zachowuje security i jakość.

---

# 22. MASTER PROMPT DLA AI PROGRAMUJĄCEGO

```text
Jesteś głównym programistą projektu AI Operator.

Przeczytaj cały aktualny MASTER PROJECT CONTEXT i traktuj go jako źródło prawdy
dla architektury, bezpieczeństwa, aktualnego stanu i roadmapy.

Najważniejsza zasada:
MODEL PROPOSES. APPLICATION DECIDES. AUTHORIZED APPLICATION SERVICES EXECUTE.

AI Operator jest nadrzędnym właścicielem durable state, security, lifecycle,
task state, workspace, verification i recovery.

AI Manager jest warstwą decyzji i planowania. Nie jest bezpośrednim wykonawcą.
Development Agent jest wykonawcą developmentu przez kontrolowany adapter.

Nie omijaj:
- PermissionEngine;
- ApprovalService;
- ToolExecutionService;
- TaskStateMachine;
- VerificationGate;
- Workspace/Git safety;
- execution fencing.

Nie pozwalaj modelowi kontrolować task_id, run_id, execution_id, workspace,
Git, verification, attempts, execution tokens ani innych application-owned
security/lifecycle fields.

Nie twórz generic execute-anything.
Nie twórz arbitrary shell access.
Nie wykonuj realnych płatności/przelewów.
Nie automatyzuj browser/computer/email/messaging przed zaplanowanym etapem.

Aktualny główny milestone:
AUTONOMOUS CODING MVP.

Aktualny etap:
STAGE 9.6 — AI Manager → Controlled Execution.

Po 9.6 kolejność priorytetowa:
1. Kilo Code Integration
2. Kilo session/resume/recovery
3. real project E2E
4. minimal usable Control UI
5. Autonomous Coding MVP gate
6. Memory / Development History
7. File Agent
8. Browser / Research
9. Email / Calendar / Messaging
10. Automations / Scheduler / Events / Notifications
11. Computer Use
12. Mobile / Remote Control
13. Multi-model routing / provider registry
14. proactive monitoring / watchlists
15. Agent Teams
16. self-improvement proposals

Dla każdego etapu:
1. zbadaj istniejący kod;
2. potwierdź aktualną architekturę;
3. przygotuj ograniczony plan;
4. implementuj tylko zakres etapu;
5. wykonaj odpowiednie testy;
6. napraw błędy;
7. dopiero na końcowej bramce wykonaj pełną regresję;
8. wykonaj hostile audit dla krytycznych etapów;
9. wygeneruj raport;
10. nie zgłaszaj CLOSED bez dowodów.

Reasoning level:
LOW domyślnie dla prostych implementacji i testów;
MEDIUM dla trudnej implementacji/debugowania;
HIGH tylko dla architektury, trudnego security i final hostile audit.
```

---

# 23. PRAKTYCZNY STATUS PROJEKTU

## 🟢 GOTOWE

- Security Foundation;
- Permission / Tool execution boundaries;
- Audit;
- Autonomous Development Core v1;
- task state + persistence;
- DAG planning/execution;
- isolated workspaces/Git;
- Verification Engine;
- recovery/fencing;
- autonomous repair;
- REST control plane;
- real local Ollama coding E2E;
- AI Manager contract;
- real Ollama/OpenRouter AI Manager providers;
- AI Manager → PlanManager bridge;
- capability/policy gate;
- Approval Foundation.

## 🟡 AKTUALNIE ROBIMY

**Stage 9.6 — AI Manager → Controlled Execution.**

## 🔴 NASTĘPNE

1. Kilo Code Integration;
2. Kilo session/resume/recovery;
3. real project E2E;
4. minimal Control UI;
5. Autonomous Coding MVP final gate;
6. Memory / History;
7. File Agent;
8. Browser / Research;
9. Email / Calendar / Messaging;
10. Automations / Scheduler / Notifications;
11. Computer Use;
12. Mobile / Remote Control;
13. dalsze docelowe capabilities.

## 🎯 NAJBLIŻSZY MILESTONE

**AUTONOMOUS CODING MVP** — pierwsza wersja AI Operatora, którą użytkownik może praktycznie wykorzystać do autonomicznego prowadzenia kontrolowanego projektu programistycznego.

## 📊 PRAKTYCZNY STAN

- Autonomous Development Core: **gotowy v1**
- AI Manager foundation/providers: **gotowe**
- Controlled AI Manager execution: **w budowie**
- Kilo application integration: **jeszcze niegotowa**
- Autonomous Coding MVP: **w budowie**
- Full AI Operator: **długoterminowy roadmap**

Procentów ukończenia nie należy traktować jako formalnego KPI. Najważniejszy jest stan milestone'ów i spełnienie Definition of Done.

## ⚠️ GŁÓWNE RYZYKA

- poprawne powiązanie approval z dokładnym zakresem execution;
- replay/concurrency przy approval consumption;
- utrzymanie fail-closed security;
- niezawodność Kilo i jego sesji;
- zachowanie durable state przy crash/context loss;
- zasobożerność lokalnych modeli;
- zbyt wczesne rozszerzenie scope na full Computer Use;
- przedwczesne budowanie dużego UI zamiast domknięcia core.

---

# 24. DEFINICJA AUTONOMOUS CODING MVP — FINAL GATE

MVP można uznać za gotowe dopiero wtedy, gdy użytkownik może:

1. podać request lub plan projektu;
2. wskazać dozwolony folder projektu;
3. uruchomić kontrolowany execution;
4. AI Manager podejmie decyzję;
5. policy/capability/approval poprawnie zadziałają;
6. system utworzy/persistuje plan;
7. tasks wykonają się w poprawnej kolejności;
8. Development Agent rzeczywiście zmieni kod;
9. agent może korzystać tylko z autoryzowanych capabilities;
10. Verification wykona wymagane checks;
11. błędy mogą uruchomić bounded repair;
12. crash/timeout/cancel nie uszkodzą durable state ani nie stworzą unsafe workspace reuse;
13. Kilo może stracić sesję, a Operator nadal zachowa stan;
14. nowa sesja może bezpiecznie wznowić task;
15. commit nastąpi dopiero po wymaganej weryfikacji;
16. wynik i historia będą dostępne użytkownikowi;
17. użytkownik będzie mógł zatrzymać lub przejąć kontrolę;
18. final hostile audit nie pozostawi CRITICAL/HIGH/MEDIUM;
19. pełna regresja przejdzie;
20. rzeczywisty projekt E2E potwierdzi działanie całej ścieżki.

Wtedy system będzie można nazwać pierwszą praktycznie używalną wersją autonomicznego AI Operatora do developmentu.

---

# 25. DEFINICJA KOŃCOWEGO AI OPERATORA

Pełny docelowy system będzie umożliwiał użytkownikowi m.in.:

- rozmowę tekstową i głosową;
- pamięć;
- zarządzanie projektami;
- autonomiczny development;
- pliki;
- web research;
- e-mail;
- kalendarz;
- komunikatory;
- automatyzacje;
- monitoring;
- notifications;
- computer use;
- mobile/remote control;
- wiele modeli i providerów;
- agent teams;
- kontrolowane self-improvement proposals.

Wszystkie te funkcje muszą być podłączane jako kontrolowane capabilities z własnymi permission/policy/approval boundaries.

Nie należy budować ich wszystkich przed ukończeniem Autonomous Coding MVP.

---

# 26. SOURCE OF TRUTH — ZASADA AKTUALIZACJI DOKUMENTU

Ten dokument jest żywym źródłem prawdy.

Po każdej ważnej zmianie architektury należy:

1. zwiększyć wersję dokumentu;
2. zaktualizować datę;
3. zaktualizować Current Verified State;
4. zaktualizować roadmapę;
5. usunąć nieaktualne twierdzenia;
6. dopisać nowe twarde reguły architektoniczne;
7. zachować ważne decyzje historyczne;
8. nie pozostawiać twierdzeń sugerujących, że niezamknięty etap jest zamknięty.

Stare numery Stage mogą pozostać w sekcji historycznej, ale roadmapa wykonawcza ma być oparta o aktualne milestone'y.

---

# KONIEC DOKUMENTU

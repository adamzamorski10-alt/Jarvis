# AI OPERATOR — MASTER PROJECT CONTEXT
## Kompletny blueprint projektu dla AI programującego i autonomicznego

**Status:** aktywny dokument źródłowy prawdy; Foundation oraz fundament autonomicznego developmentu są częściowo zrealizowane i zweryfikowane  
**Data aktualizacji:** 2026-09-06  
**Wersja dokumentu:** 2.0  
**Poprzednia wersja:** 1.1 z 2026-08-27

---

# 1. CZYM JEST TEN PROJEKT?

AI Operator to lokalny, desktopowy system osobistego asystenta AI dla Windows.

Nie jest to zwykły chatbot. Docelowo jest to modularny system agentowy, który ma potrafić:

- zarządzać komputerem użytkownika w granicach polityk bezpieczeństwa;
- analizować pliki, dokumenty i foldery;
- przeszukiwać lokalne dane;
- zarządzać projektami programistycznymi;
- analizować, tworzyć i naprawiać kod;
- korzystać z przeglądarki;
- wykonywać zadania automatycznie podczas nieobecności użytkownika;
- analizować e-maile, wiadomości i powiadomienia;
- integrować różne API i usługi;
- posiadać trwałą pamięć;
- korzystać z lokalnych i zewnętrznych modeli AI;
- współpracować z zewnętrznym Development Agentem, początkowo Kilo Code;
- automatycznie reagować na błędy, timeouty, puste odpowiedzi, limity i awarie agenta;
- tworzyć wiele niezależnych projektów/zadań równolegle, o ile zasoby komputera i polityki na to pozwalają;
- w przyszłości umożliwiać pełną zdalną kontrolę z telefonu i innych komputerów.

Główna zasada projektu:

> AI nie dostaje bezpośrednio nieograniczonej kontroli nad komputerem.
> AI planuje i wybiera działania, a system bezpieczeństwa decyduje,
> czy konkretna akcja może zostać wykonana automatycznie.

Projekt ma być rozwijany przede wszystkim w bezpłatnym modelu wykorzystującym lokalne komponenty oraz darmowe limity usług, tam gdzie jest to możliwe.

---

# 2. NAJWAŻNIEJSZY CEL PROJEKTU

Końcowym celem jest system, któremu użytkownik może przekazać plan projektu, np.:

> „Utwórz nowy projekt w `C:\AIProjects\SklepInternetowy` na podstawie załączonego pliku z planem. Zaplanuj pracę, napisz kod, testuj go, naprawiaj błędy i doprowadź projekt do zweryfikowanego stanu.”

Docelowy przepływ:

```text
USER
  │
  │ plan / opis / plik z planem
  ▼
GEMINI MANAGER
  │
  │ analiza + dekompozycja
  ▼
AI OPERATOR
  │
  │ zadania + polityki + stan
  ▼
DEVELOPMENT AGENT
  │
  │ początkowo Kilo Code
  ▼
WORKSPACE PROJEKTU
  │
  ├── pliki
  ├── kod
  ├── testy
  └── Git
  │
  ▼
VERIFICATION ENGINE
  │
  ├── testy
  ├── build
  ├── lint
  ├── runtime smoke
  └── dodatkowe kontrole
  │
  ├── PASS ───────────────► COMMIT
  │
  └── FAIL ─► ANALIZA ─► REPAIR ─► RETEST
                           │
                           └── powtarzaj w granicach budżetu
```

AI Operator jest warstwą nadrzędną. Gemini nie ma być właścicielem stanu projektu, a Kilo nie ma być właścicielem procesu autonomicznego.

---

# 3. AKTUALNY ZWERYFIKOWANY STAN PROJEKTU

## 3.1 Zrealizowane fundamenty bezpieczeństwa i narzędzi

Zweryfikowano i zamknięto:

- Stage 5.1–5.3 — Tool Registry, Permission Engine, grants i powiązane fundamenty;
- Stage 5.4 — Audit Log;
- Stage 5.5.1 — BaseTool, ToolImplementationRegistry, ToolExecutor;
- Stage 5.5.2 — security boundary Tool Execution;
- Stage 5.5.3.1–5.5.3.3 — AIResponse/provider normalization/agent loop/tool-call correlation/provider tool calling;
- Stage 5.5.4 — Approval Security Boundary;
- Stage 5.5.5 — End-to-End Security Integration.

Najważniejsza zasada:

```text
AI
 ↓
Tool Registry / kontrakt
 ↓
Permission Engine / Approval
 ↓
ToolExecutionService
 ↓
ToolExecutor
 ↓
Audit Log
```

Nie wolno tworzyć bocznej ścieżki omijającej tę granicę.

## 3.2 Zweryfikowane etapy autonomicznego developmentu

### Stage 6.1 — Architecture Discovery
STATUS: CLOSED

### Stage 6.2 — Development Task Model & Repository
STATUS: CLOSED

Commit:
`2cf00bb94b419812dd66289b4f6208cb17646549`

### Stage 6.3 — Task State Machine & Transition Engine
STATUS: CLOSED

Commit:
`536f6b4a53aeebefec0faebef7b5aee1dd0d78bd`

Obowiązuje dokładnie 10 stanów:

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

Nie istnieje stan `RECOVERING`.

### Stage 6.4 — Workspace & Git Checkpoint Manager
STATUS: CLOSED

Commit:
`e2e46b4`

Najważniejsze zasady:

- autonomiczna praca odbywa się w dedykowanym Git worktree;
- primary worktree jest chroniony;
- destructive reset może dotyczyć wyłącznie dedykowanego autonomicznego worktree;
- brak ślepego `git add .` / `git add -A`;
- staged file set jest weryfikowany;
- commit SHA jest sprawdzany;
- brak automatycznego merge do `main`.

### Stage 6.5 — Verification Gate Foundation
STATUS: CLOSED

Commit:
`152dacc21944e3eb00b8e9f7c49deddb0cfde029`

Verification Gate jest warunkiem sukcesu przed commitem.

### Stage 6.6 — Autonomous Task Orchestrator
STATUS: CLOSED

Commit:
`564ba8833f8e1940abf5d8283b6afc10b400b656`

Zweryfikowane m.in.:

- durable task state;
- legal state transitions;
- atomic claim próby;
- concurrency safety;
- bounded attempts;
- retry;
- verification before commit;
- dedicated worktree;
- provider-neutral `DevelopmentAgent`;
- brak bezpośredniego dostępu do ToolExecutor/BaseTool/PermissionEngine;
- brak integracji Gemini/Kilo/REST na tym etapie.

Szczególnie ważne: claim próby jest atomowy. Nie wolno powrócić do schematu:

```text
increment attempt
   ↓
dopiero później CAS RUNNING
```

Zamiast tego claim statusu i inkrementacja próby muszą być wykonywane atomowo.

### Stage 6.7 — Crash & Timeout Recovery Manager
STATUS: CLOSED

Commit:
`b4a7a3bc02c68b16dcb6396d0bbba35043b3b190`

Zweryfikowano:

- heartbeat;
- stale detection;
- durable recovery claim;
- recovery lease;
- recovery attempt limits;
- concurrency;
- terminal-state protection;
- execution fencing;
- odporność na przejęcie recovery przez drugiego recoverera;
- brak resurrection terminal task.

Kluczowa zasada execution fencing:

```text
normal execution:
RUNNING + execution_token = A + recovery_owner = NULL

recovery claim:
RUNNING + execution_token = NULL + recovery_owner = R

nowa próba:
RUNNING + execution_token = B + recovery_owner = NULL
```

Stary token A nie może po recovery ponownie wysłać heartbeat ani odzyskać prawa do wykonania.

---

# 4. AKTUALNY ETAP — STAGE 6.8

## Stage 6.8 — Autonomous Run Controller

STATUS: **NOT CLOSED — ACTIVE REMEDIATION**

Ostatni bezpieczny commit bazowy:

`b4a7a3bc02c68b16dcb6396d0bbba35043b3b190`

Ostatni hostile audit:

`STAGE_6_8_FINAL_EXECUTION_SAFETY_FAIL`

## Dlaczego Stage 6.8 nie został zaakceptowany?

Testy przechodziły, ale audyt znalazł problemy architektoniczne, których nie wolno zaakceptować tylko na podstawie zielonych testów.

Najważniejsze wykryte problemy:

1. `workspace_owner_token` był w pewnym momencie martwą infrastrukturą — zdefiniowany mechanizm nie był rzeczywiście używany przez ścieżkę wykonawczą.
2. Anulowanie/abort mogło opierać się na lokalnym `asyncio.Event`, co samo w sobie nie gwarantuje zatrzymania niekooperatywnego procesu/agenta.
3. Występowała możliwość kontynuowania wykonania po timeout/cancel.
4. Cleanup workspace nie może następować tylko dlatego, że minęło arbitralne 10 sekund.
5. `shutil.rmtree` nie może być fallbackiem wykonywanym wobec workspace, którego właściciel wykonania może nadal aktywnie pisać.
6. Nie wystarcza dowód, że stan DB jest poprawny. Musi być bezpieczna również warstwa filesystem/execution.
7. Testy określane jako non-cooperative nie mogą w rzeczywistości kończyć się w sposób kooperacyjny i udawać dowodu wymuszonego zatrzymania.
8. Timeout nie może oznaczać „execution na pewno się zakończył”, jeżeli nie ma na to dowodu.
9. Kolejna próba nie może wejść do workspace, który może być nadal używany przez poprzednią próbę.
10. Recovery Manager nie może usuwać aktywnego workspace tylko dlatego, że recovery uznał poprzednie wykonanie za stale.

## Wymagany model Stage 6.8

Bezpieczna sekwencja:

```text
CANCEL / ABORT / TIMEOUT
        │
        ▼
durable execution fence
        │
        ▼
signal/cancel/terminate execution
        │
        ▼
potwierdzenie zakończenia
        │
   ┌────┴────┐
   │         │
  STOP     NIE STOP
   │         │
   ▼         ▼
cleanup    RETAIN / ISOLATE
workspace  workspace
   │         │
   ▼         ▼
next       recovery / cleanup
attempt    pending
```

Niedopuszczalna sekwencja:

```text
cancel
 ↓
cleanup
 ↓
delete workspace
 ↓
agent nadal działa
```

Jeżeli agent/proces jest naprawdę niekooperatywny i nie można go bezpiecznie zakończyć, system musi zachować/isolate workspace do czasu uzyskania pewności, że execution już nie działa.

## Finalny warunek zamknięcia Stage 6.8

Akceptacja dopiero po uzyskaniu:

`STAGE_6_8_FINAL_EXECUTION_SAFETY_PASS`

i po niezależnym hostile audit.

---

# 5. GŁÓWNY ROADMAP — NOWA WERSJA

## STAGE 5 — SECURITY FOUNDATION
STATUS: CLOSED

5.1 Tool Registry  
5.2 Permission Engine  
5.3 Grants / Approval primitives  
5.4 Audit Log  
5.5.1–5.5.3 Tool execution + AI/provider foundation  
5.5.4 Approval Security Boundary  
5.5.5 End-to-End Security Integration

---

## STAGE 6 — AUTONOMOUS DEVELOPMENT FOUNDATION

### 6.1 Architecture Discovery
CLOSED

### 6.2 Development Task Model & Repository
CLOSED

### 6.3 Task State Machine & Transition Engine
CLOSED

### 6.4 Workspace & Git Checkpoint Manager
CLOSED

### 6.5 Verification Gate Foundation
CLOSED

### 6.6 Autonomous Task Orchestrator
CLOSED

### 6.7 Crash & Timeout Recovery Manager
CLOSED

### 6.8 Autonomous Run Controller
ACTIVE / NOT CLOSED

Cel:

- bezpieczne uruchamianie runów;
- cancellation;
- abort;
- timeout;
- execution fencing;
- workspace ownership;
- cleanup;
- cross-attempt isolation;
- durable reconciliation;
- bezpieczne zachowanie przy utracie procesu.

---

# 6. STAGE 7 — VERIFICATION ENGINE

STATUS: NEXT

Cel: stworzyć pełny system określający, czy rezultat programistyczny jest rzeczywiście poprawny.

Zakres:

- check registry;
- test runner;
- lint;
- formatter checks;
- type checking;
- unit tests;
- integration tests;
- build;
- runtime smoke tests;
- security checks;
- repository consistency;
- diff inspection;
- result aggregation;
- PASS/FAIL;
- structured verification report.

Verification Engine musi działać niezależnie od Kilo i Gemini.

Zasada:

> Kod nie jest „gotowy”, ponieważ agent twierdzi, że jest gotowy. Kod jest gotowy dopiero po przejściu wymaganych checks.

---

# 7. STAGE 8 — GEMINI MANAGER

STATUS: FUTURE

To etap, w którym zostanie podłączone Google Gemini API.

Gemini ma być przede wszystkim:

- Managerem;
- Plannerem;
- analizatorem wymagań;
- dekompozytorem dużych projektów;
- generatorem planów;
- analizatorem wyników Verification Engine;
- analizatorem błędów;
- generatorem repair planów;
- koordynatorem kolejnych tasków.

Gemini nie powinien otrzymać bezpośredniego, niekontrolowanego dostępu do systemu.

Docelowy kontrakt:

```text
USER PLAN / FILE
      ↓
Gemini Manager
      ↓
structured Project Plan
      ↓
AI Operator
```

API key:

- nie może być zapisany w repozytorium;
- nie może trafić do zwykłych logów;
- nie może być bezpośrednio przekazywany do Kilo jako część promptu;
- powinien być przechowywany przez bezpieczny mechanizm konfiguracji/secret storage.

---

# 8. STAGE 9 — KILOCODE INTEGRATION

STATUS: FUTURE

Cel: podłączenie Kilo Code jako pierwszego rzeczywistego Development Agenta.

Architektura:

```text
DevelopmentAgent
       │
       ▼
KiloCodeAgent Adapter
       │
       ▼
Kilo Code
```

AI Operator nie powinien zakładać, że Kilo jest jedynym możliwym agentem.

Docelowo możliwe są:

```text
DevelopmentAgent
 ├── KiloCodeAgent
 ├── OtherAgent
 └── FutureAgent
```

Kilo powinno otrzymywać zadania ograniczone do konkretnego workspace i konkretnego taska.

Nie wysyłamy jednego gigantycznego prompta dla całego projektu, jeżeli można projekt rozbić na mniejsze zadania.

---

# 9. OBSŁUGA KILO CODE — WAŻNA FUNKCJA NIEZAWODNOŚCI

Projekt musi zakładać, że Kilo może być zawodny.

Przykładowe problemy:

```text
Compaction worker returned an empty response
rate limit
context limit
provider error
tool error
timeout
crash
brak odpowiedzi
długi brak odpowiedzi
częściowe wykonanie
```

AI Operator nie może uznać takich sytuacji za sukces.

## Wymagany mechanizm

```text
KILO
 │
 ├── SUCCESS
 │      ↓
 │   verify result
 │
 ├── EMPTY RESPONSE
 │      ↓
 │   inspect workspace + task state
 │      ↓
 │   retry / continue / new session
 │
 ├── RATE LIMIT
 │      ↓
 │   backoff
 │      ↓
 │   retry
 │
 ├── CONTEXT / COMPACTION ERROR
 │      ↓
 │   zachowaj stan
 │      ↓
 │   nowa sesja
 │      ↓
 │   resume
 │
 ├── TOOL ERROR
 │      ↓
 │   analyse
 │      ↓
 │   repair
 │
 └── CRASH / TIMEOUT
        ↓
     recovery
```

## Najważniejsza zasada

Kilo może stracić kontekst.

AI Operator nie może stracić stanu projektu.

Źródłem prawdy są:

- Task DB;
- Git;
- workspace state;
- verification results;
- execution state;
- project plan;
- durable reports/history.

Nie wolno polegać na pamięci pojedynczego chatu Kilo.

---

# 10. SESJE KILO I AUTOMATYCZNE „NOWE CHATY”

Użytkownik obecnie ręcznie rozwiązuje problemy Kilo poprzez:

- ponowienie prompta;
- „czy wszystko już zrobiłeś? Jak nie to dokończ”;
- wysłanie tego samego prompta ponownie;
- utworzenie nowego chatu.

Docelowo Operator ma wykonywać analogiczną procedurę automatycznie.

Przykład:

```text
Kilo Session A
      ↓
context/compaction problem
      ↓
save durable state
      ↓
inspect Git/workspace/task
      ↓
Session A marked failed/interrupted
      ↓
create Session B
      ↓
send compact resume context
      ↓
continue task
```

Resume context powinien zawierać co najmniej:

- task_id;
- project_id;
- workspace identity;
- execution identity;
- Git SHA;
- aktualny TaskStatus;
- wykonane kroki;
- wynik ostatniej próby;
- ostatni błąd;
- verification result;
- następny wymagany krok.

---

# 11. STAGE 10 — AUTONOMOUS DEVELOPMENT LOOP

STATUS: DOCZELOWY KLUCZOWY ETAP

Dopiero tutaj wszystkie główne elementy zostaną połączone w jeden autonomiczny cykl.

```text
USER
 ↓
PLAN / FILE
 ↓
GEMINI MANAGER
 ↓
PROJECT PLAN
 ↓
TASK QUEUE
 ↓
AI OPERATOR
 ↓
KILO
 ↓
CODE CHANGES
 ↓
VERIFICATION
 ├── PASS → COMMIT
 └── FAIL
       ↓
   GEMINI ANALYSIS
       ↓
   REPAIR TASK
       ↓
   KILO
       ↓
   VERIFICATION
```

Cykl działa do momentu:

- PASS;
- osiągnięcia limitu prób;
- wymaganej zgody użytkownika;
- nierozwiązywalnego błędu;
- braku zasobów;
- provider outage;
- bezpiecznego PAUSED/FAILED.

---

# 12. TWORZENIE CAŁYCH PROJEKTÓW Z PLIKU Z PLANEM

To jest jeden z głównych docelowych przypadków użycia.

Użytkownik może wskazać:

```text
Plan projektu:
C:\AIProjects\plans\my_project_plan.md
```

oraz:

```text
Folder docelowy:
C:\AIProjects\MyProject
```

Docelowy przepływ:

```text
PLAN FILE
   ↓
AI Operator odczytuje plan
   ↓
Gemini analizuje plan
   ↓
Gemini tworzy Project Plan
   ↓
AI Operator tworzy Project
   ↓
dekompozycja na tasks
   ↓
dedykowane workspaces
   ↓
Kilo wykonuje tasks
   ↓
Verification
   ↓
repair/retry
   ↓
commit
   ↓
next task
   ↓
PROJECT COMPLETE
```

Ważne:

- plan użytkownika jest wejściem, nie instrukcją omijającą security;
- ścieżka docelowa musi przejść przez politykę folderów;
- AI nie może samowolnie uzyskać dostępu do całego dysku;
- każdy projekt ma własny stan i historię.

---

# 13. WIELE PROJEKTÓW JEDNOCZEŚNIE

System docelowo ma wspierać wiele niezależnych projektów.

Przykład:

```text
AI Operator
 │
 ├── Project A
 │    ├── Task A1
 │    └── Task A2
 │
 ├── Project B
 │    ├── Task B1
 │    └── Task B2
 │
 └── Project C
      └── Task C1
```

Każdy projekt/run musi mieć niezależne:

- project_id;
- task_id;
- execution identity;
- workspace identity;
- Git branch/worktree;
- state;
- verification result;
- logs;
- history.

Równoległość musi być ograniczana przez:

- CPU;
- RAM;
- VRAM;
- liczbę procesów;
- limity providerów;
- politykę użytkownika.

Nie wolno uruchamiać wielu dużych modeli lokalnych tylko dlatego, że istnieją równoległe zadania.

---

# 14. WORKSPACE I BEZPIECZEŃSTWO WIELOPROJEKTOWE

Każde wykonanie powinno mieć jasno określoną przestrzeń roboczą.

Preferowany model:

```text
project
   │
   ├── execution A → workspace A
   ├── execution B → workspace B
   └── execution C → workspace C
```

Workspace nie może być usunięty, jeżeli istnieje możliwość aktywnego wykonania.

Kolejna próba nie może wykorzystywać workspace potencjalnie nadal kontrolowanego przez poprzednią próbę.

Destrukcyjne operacje filesystem/Git muszą przechodzić przez jedną świadomą warstwę bezpieczeństwa.

---

# 15. GIT — ZASADY AUTONOMICZNEGO DEVELOPMENTU

AI może wykonywać commity w dedykowanym środowisku autonomicznym.

Początkowo:

```text
main
  │
  └── autonomous/project-X/task-Y
             │
             ├── checkpoint
             ├── repair
             └── final commit
```

**AUTO MERGE = OFF**

AI nie może automatycznie scalać zmian do `main` bez osobnej polityki i późniejszego etapu.

Każdy commit musi mieć:

- zweryfikowany stan;
- poprawny SHA;
- odpowiedni zakres plików;
- brak niezamierzonych zmian.

Nie wolno używać:

```text
git commit --no-verify
```

jako sposobu obchodzenia verification/hooks.

---

# 16. TASK STATE MACHINE — TWARDY KONTRAKT

Dozwolone stany:

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

Nie dodawać `RECOVERING`, chyba że przyszła osobna decyzja architektoniczna zmieni ten kontrakt.

Podstawowe przejścia:

```text
PENDING → RUNNING

RUNNING → VERIFYING
RUNNING → PAUSED
RUNNING → FAILED
RUNNING → CANCELLED
RUNNING → ABORTED

VERIFYING → PASSED
VERIFYING → REPAIR_REQUIRED
VERIFYING → FAILED
VERIFYING → CANCELLED
VERIFYING → ABORTED

PASSED → COMMITTED
PASSED → REPAIR_REQUIRED
PASSED → FAILED
PASSED → CANCELLED
PASSED → ABORTED

REPAIR_REQUIRED → RUNNING
REPAIR_REQUIRED → FAILED
REPAIR_REQUIRED → CANCELLED
REPAIR_REQUIRED → ABORTED

PAUSED → RUNNING
PAUSED → CANCELLED
PAUSED → ABORTED

FAILED → REPAIR_REQUIRED
FAILED → CANCELLED
FAILED → ABORTED

ABORTED → REPAIR_REQUIRED

CANCELLED → terminal
COMMITTED → terminal
```

Każda zmiana stanu musi przechodzić przez `TaskStateMachine`.

---

# 17. PAMIĘĆ I HISTORIA DEVELOPMENTU

## Session Memory

Tylko bieżąca sesja.

## User Preferences

Preferencje użytkownika.

## Project Memory

Pamięć konkretnego projektu:

```text
PROJECT
├── description
├── goals
├── technologies
├── architecture
├── decisions
├── tasks
├── bugs
├── ideas
├── files
├── changelog
└── AI context
```

## Development History

Docelowo historia:

- promptów;
- planów;
- wyników Kilo;
- błędów;
- verification;
- repair attempts;
- commitów;
- recovery;
- decyzji Gemini;
- wyników użytkownika.

## Secrets

Sekrety nigdy nie są częścią zwykłej pamięci modelu.

---

# 18. GEMINI — ROLA W SYSTEMIE

Gemini nie powinien być jedynym „mózgiem” całego systemu.

Jego główne role:

```text
requirements analysis
       ↓
project planning
       ↓
task decomposition
       ↓
error analysis
       ↓
repair planning
       ↓
result interpretation
       ↓
next-task planning
```

AI Operator zachowuje:

- durable state;
- security;
- execution control;
- task transitions;
- workspace control;
- verification authority;
- recovery authority.

---

# 19. KILO CODE — ROLA W SYSTEMIE

Kilo jest wykonawcą developmentu, a nie nadrzędnym kontrolerem systemu.

Kilo:

- analizuje repozytorium;
- edytuje kod;
- tworzy pliki;
- uruchamia dozwolone działania developmentowe;
- testuje;
- naprawia kod;
- raportuje wynik.

AI Operator:

- decyduje, jaki task Kilo ma wykonać;
- kontroluje workspace;
- kontroluje lifecycle;
- kontroluje timeout;
- obsługuje recovery;
- weryfikuje wynik;
- decyduje, czy można zrobić commit.

---

# 20. OBSŁUGA AWARII PROVIDERÓW I AGENTÓW

Każdy provider/agent musi mieć klasyfikację błędów.

Przykładowo:

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

- retryable;
- backoff;
- max retries;
- czy można stworzyć nową sesję;
- czy należy wznowić;
- czy potrzebny jest repair;
- czy task ma zostać PAUSED/FAILED.

Nie wolno stosować nieskończonego retry.

---

# 21. BUDŻETY I OCHRONA PRZED PĘTLĄ

Każdy autonomiczny run powinien mieć limity:

- max attempts;
- max repair attempts;
- max recovery attempts;
- max runtime;
- max provider retries;
- max empty responses;
- max context/session restarts;
- opcjonalnie max koszt/limit tokenów dla zewnętrznego API.

Przykład:

```text
task
 ↓
attempt 1
 ↓
FAIL
 ↓
repair 1
 ↓
attempt 2
 ↓
FAIL
 ↓
repair 2
 ↓
attempt limit
 ↓
PAUSED / FAILED
```

System nie może wejść w nieskończoną pętlę:

```text
Kilo → error → retry → error → retry → ...
```

---

# 22. SYSTEM AUTONOMII

## TRYB 1 — MANUALNY

AI analizuje i proponuje.

## TRYB 2 — NADZOROWANY

Niskie ryzyko automatycznie, wyższe poziomy wymagają zgody.

## TRYB 3 — AUTONOMIA KONTROLOWANA

Większość wcześniej dozwolonych działań automatycznie.

## TRYB 4 — PEŁNA AUTONOMIA W GRANICACH POLITYK

AI może pracować podczas nieobecności użytkownika.

„Pełna autonomia” nigdy nie oznacza braku zasad.

---

# 23. POZIOMY RYZYKA

### GREEN

- odczyt dozwolonych plików;
- analiza;
- tworzenie raportów;
- praca w dedykowanym workspace.

### YELLOW

- modyfikacja istniejących plików;
- uruchamianie aplikacji;
- zmiany konfiguracji niekrytycznej.

### ORANGE

- instalacja pakietów;
- push;
- wysyłanie wiadomości;
- działania o większym wpływie.

### RED

- trwałe usuwanie;
- zmiany bezpieczeństwa;
- eksport sekretów;
- transakcje finansowe;
- nieznany kod poza zatwierdzonym środowiskiem.

RED wymaga bezpośredniej zgody, chyba że przyszła osobna polityka bezpieczeństwa ustanowi inaczej.

---

# 24. FILE AGENT

Docelowe narzędzia:

```text
files.list
files.search
files.read_metadata
files.read_text
files.create
files.copy
files.move
files.rename
files.delete_to_recycle_bin
```

Permanent delete nie jest domyślnym mechanizmem.

Foldery muszą być objęte polityką dostępu.

---

# 25. CODING AGENT — DOCELOWY CYKL

```text
REQUEST
 ↓
ANALYZE PROJECT
 ↓
CREATE PLAN
 ↓
CREATE / SELECT WORKSPACE
 ↓
EXECUTE TASK
 ↓
RUN TESTS
 ↓
ANALYZE ERRORS
 ↓
REPAIR
 ↓
RETEST
 ↓
VERIFICATION
 ↓
REPORT
 ↓
COMMIT
```

Nie wolno uznać zadania za zakończone tylko dlatego, że Kilo napisało:

> done

---

# 26. PROJECT SYSTEM

Projekt jest obiektem pierwszej klasy.

Minimalnie:

```text
projects
project_tasks
project_decisions
project_notes
project_memories
project_files
project_runs
project_integrations
```

Docelowo każdy projekt posiada:

- Overview;
- Tasks;
- Files;
- Memory;
- Architecture;
- Decisions;
- Bugs;
- AI Activity;
- Git;
- Automations;
- Verification history.

---

# 27. WIELOZADANIOWOŚĆ

System ma obsługiwać:

- wiele tasków;
- wiele projektów;
- wiele workspace;
- wiele executionów.

Jednak równoległość nie może omijać:

- durable state;
- atomic claims;
- execution fencing;
- workspace ownership;
- verification;
- permission model.

Procesy mogą działać równolegle, ale nie mogą współdzielić tego samego execution ownership.

---

# 28. REMOTE ACCESS

STATUS: FUTURE

Nie implementować przed stabilnym lokalnym autonomicznym core.

Docelowo:

- telefon jako pełny panel sterowania;
- akceptowanie Approval Requests;
- start/stop/pause;
- podgląd projektów;
- podgląd zadań;
- logi;
- powiadomienia;
- bezpieczny tunnel/VPN;
- zdalne zarządzanie.

REST/remote API pozostaje poza Stage 6.

---

# 29. TESTY — TWARDY STANDARD PROJEKTU

Każdy istotny etap wymaga:

## Unit tests

Dla logiki domenowej.

## Integration tests

Dla bazy, repozytoriów, adapterów i granic modułów.

## Concurrency tests

Preferowane:

- real file-backed SQLite;
- WAL;
- niezależne sesje;
- `asyncio.gather`;
- bez sztucznej serializacji.

## Failure injection

Testy muszą sprawdzać:

- wyjątki;
- timeout;
- crash;
- empty response;
- provider failure;
- cancellation;
- abort;
- stale execution;
- recovery takeover.

## Repeated runs

Istotne testy powinny być uruchamiane wielokrotnie, np. 10×, gdy jest to wymagane przez etap.

## Full regression

Po zmianach autonomicznego core należy uruchamiać pełną regresję dostępnych testów.

## Hostile audit

Przed zamknięciem każdego krytycznego etapu musi istnieć osobny hostile audit.

Zielone testy nie wystarczają.

---

# 30. FORMALNE RAPORTY TECHNICZNE

Każdy większy etap powinien tworzyć:

```text
STAGE_X.Y_IMPLEMENTATION_REPORT.md
STAGE_X.Y_FINAL_TECHNICAL_REPORT.md
```

Raport musi zawierać:

- baseline HEAD;
- aktualny HEAD;
- changed files;
- architekturę;
- security boundaries;
- state-machine compatibility;
- concurrency;
- cancellation;
- timeout;
- workspace ownership;
- execution fencing;
- recovery compatibility;
- test inventory;
- komendy testów;
- wyniki;
- repeated runs;
- full regression;
- known limitations;
- remaining risks;
- Git state;
- final verdict.

Raport nie zastępuje audytu ani testów.

---

# 31. GIT SAFETY

AI programujące:

- nie wykonuje reset/rebase/merge bez wyraźnego zakresu zadania;
- nie usuwa zmian użytkownika;
- nie używa `--no-verify` do obchodzenia zabezpieczeń;
- nie robi commitów poza ustalonym zakresem;
- nie pushuje automatycznie do remote, jeżeli polityka tego nie zezwala;
- nie scala automatycznie do main.

Przed każdym krytycznym etapem:

```text
git status
git rev-parse HEAD
git diff
git diff --cached
```

muszą być możliwe do zweryfikowania.

---

# 32. SEKRETY I API

Klucze:

- Gemini API key;
- inne provider API keys;
- tokeny;
- hasła;

nie mogą być commitowane do repozytorium.

Nie wolno umieszczać ich w:

- source code;
- zwykłych raportach;
- Git history;
- debug logs;
- promptach przekazywanych innym agentom bez konieczności.

Logowanie musi maskować sekrety.

---

# 33. DYNAMICZNY CORE

Obowiązuje wcześniejsza zasada:

- Python Core używa dynamicznego portu przypisanego przez OS;
- frontend pobiera Core URL przez wspólny mechanizm;
- nie hardcodować portów;
- błędy HTTP nie oznaczają automatycznie `CORE OFFLINE`;
- `CoreOfflineError` jest zarezerwowany dla rzeczywistego braku połączenia.

---

# 34. OBSŁUGA BŁĘDÓW CORE / PROVIDER

Przykładowe kody:

```text
core_unreachable
core_timeout
provider_not_configured
invalid_api_key
provider_rate_limited
provider_quota_exceeded
model_not_found
provider_unavailable
http_500
internal_error
```

Błędy muszą być klasyfikowane jako:

- retryable;
- non-retryable;
- wymagające użytkownika;
- wymagające recovery.

---

# 35. INTERFEJS

Docelowy układ:

```text
┌──────────────────────────────────────────────────────────────┐
│ AI OPERATOR                                      ● AI STATUS │
├───────────────┬──────────────────────────────────────────────┤
│ Dashboard     │                                              │
│ Assistant     │               MAIN AREA                      │
│ Tasks         │                                              │
│ Agents        │                                              │
│ Files         │                                              │
│ Projects      │                                              │
│ Browser       │                                              │
│ Messages      │                                              │
│ Automations   │                                              │
│ Approvals     │                                              │
│ Activity      │                                              │
│ Settings      │                                              │
└───────────────┴──────────────────────────────────────────────┘
```

Dashboard powinien pokazywać m.in.:

- aktywne projekty;
- aktywne runy;
- task status;
- agent status;
- verification;
- błędy;
- recovery;
- approval requests;
- wykorzystanie zasobów;
- tryb autonomii.

---

# 36. DOCELOWY PRZYKŁAD UŻYCIA

Użytkownik:

```text
Utwórz projekt aplikacji finansowej.
Plan znajduje się w:
C:\AIProjects\plans\finance.md

Projekt utwórz w:
C:\AIProjects\FinanceApp

Pracuj autonomicznie.
```

Docelowy system:

```text
1. Odczytuje plan.
2. Sprawdza uprawnienia folderu.
3. Gemini analizuje plan.
4. Tworzy Project Plan.
5. Dzieli plan na Tasks.
6. Tworzy bezpieczne workspace.
7. Kilo realizuje Task 1.
8. Verification.
9. Commit.
10. Task 2.
11. Kilo ma empty response.
12. Operator sprawdza stan projektu.
13. Retry/resume lub nowa sesja.
14. Verification.
15. Task 3.
16. Kilo napotyka błąd.
17. Gemini analizuje błąd.
18. Tworzy repair task.
19. Kilo naprawia.
20. Verification.
21. Commit.
22. Kolejne taski.
23. Final verification.
24. Project COMPLETE.
```

Jeżeli Kilo zawiedzie, projekt nie może automatycznie zostać uznany za zakończony.

---

# 37. CZY SYSTEM BĘDZIE MÓGŁ TWORZYĆ KILKA PROJEKTÓW NA RAZ?

Tak — docelowo.

Przykład:

```text
Project A → execution A1 → Kilo
Project B → execution B1 → Kilo
Project C → execution C1 → Kilo
```

Każdy execution musi mieć własną tożsamość i izolację.

Równoległość może zostać ograniczona przez użytkownika:

```text
max concurrent projects = 1
max concurrent projects = 2
max concurrent projects = 4
```

oraz automatycznie przez dostępne zasoby.

---

# 38. KIEDY SYSTEM BĘDZIE „PRAKTYCZNIE AUTONOMICZNY”?

Orientacyjnie:

```text
Stage 6.8
    ↓
bezpieczne lifecycle execution
    ↓
Stage 7
    ↓
mocna weryfikacja
    ↓
Stage 8
    ↓
Gemini Manager
    ↓
Stage 9
    ↓
Kilo Code Adapter
    ↓
Stage 10
    ↓
pełna pętla autonomicznego developmentu
```

Dopiero po zakończeniu Stage 10 można rozpocząć rzeczywiste testy:

> „AI Operator samodzielnie utwórz projekt z planu w wybranym folderze i doprowadź go do działającego stanu.”

Nie należy utożsamiać ukończenia Stage 6 z gotowym autonomicznym programistą. Stage 6 buduje bezpieczny runtime autonomicznego developmentu.

---

# 39. CO JESZCZE NIE JEST GOTOWE?

Na dzień 2026-09-06:

- [ ] Stage 6.8 — final execution safety;
- [ ] Stage 7 — Verification Engine;
- [ ] Stage 8 — Gemini Manager;
- [ ] Stage 9 — Kilo Code Integration;
- [ ] Stage 10 — Autonomous Development Loop;
- [ ] Stage 11 — Development Knowledge/History DB;
- [ ] Stage 12 — Remote API;
- [ ] Stage 13 — Mobile Control;
- [ ] późniejsze rozszerzenia self-improvement.

Nie należy podłączać Gemini/Kilo produkcyjnie przed ukończeniem wymaganych fundamentów.

---

# 40. ZASADY DLA AI PROGRAMUJĄCEGO

1. Najpierw analizuj istniejący kod.
2. Nie przepisuj całych modułów bez potrzeby.
3. Nie usuwaj istniejących funkcji bez uzasadnienia.
4. Dla większych zmian przygotuj plan.
5. Implementuj etapami.
6. Testuj po implementacji.
7. Po błędzie analizuj logi i kod.
8. Nie deklaruj sukcesu bez dowodów.
9. Nie zastępuj implementacji atrapą bez wyraźnego uzasadnienia.
10. Nie dawaj modelowi nieograniczonego shell access.
11. Wszystkie skuteczne akcje muszą być możliwe do audytu.
12. Preferuj API nad GUI automation.
13. Preferuj wyspecjalizowane narzędzia nad generic shell.
14. Nowe integracje muszą respektować permission model.
15. Nie zapisuj sekretów w kodzie.
16. Zachowuj kompatybilność wsteczną.
17. Używaj Git przed dużymi zmianami.
18. Nie instaluj przypadkowych zależności.
19. Utrzymuj kod modułowy.
20. Nie twórz mikroserwisów bez potrzeby.
21. Nie omijaj ToolExecutionService.
22. Nie omijaj TaskStateMachine.
23. Nie omijaj Verification Gate.
24. Nie usuwaj workspace aktywnego execution.
25. Nie uznawaj timeout za dowód zakończenia.
26. Nie uznawaj pustej odpowiedzi Kilo za sukces.
27. Nie zakładaj, że Kilo pamięta poprzednią sesję.
28. Wszystkie krytyczne założenia potwierdzaj testem lub audytem.

---

# 41. MASTER PROMPT DLA AI PROGRAMUJĄCEGO

```text
Jesteś głównym programistą projektu AI Operator.

Przeczytaj cały MASTER_PROJECT_CONTEXT.md i traktuj go jako źródło prawdy
dla architektury, bezpieczeństwa i roadmapy.

Projekt jest lokalną aplikacją desktopową dla Windows, której docelowym celem
jest bezpieczny, autonomiczny system AI zdolny do realizacji projektów
programistycznych.

Najważniejsze zasady:

1. AI Operator jest nadrzędnym kontrolerem lifecycle.
2. Gemini jest przyszłym Managerem/Plannerem.
3. Kilo Code jest Development Agentem wykonawczym.
4. Development Agent musi pozostać provider-neutral przez adapter.
5. Tool execution zawsze przechodzi przez obowiązującą granicę bezpieczeństwa.
6. Task state changes zawsze przechodzą przez TaskStateMachine.
7. Verification PASS jest wymagany przed commit.
8. Primary worktree użytkownika jest chroniony.
9. Workspace aktywnego execution nie może być usuwany.
10. Timeout nie oznacza automatycznie zakończenia execution.
11. Empty response Kilo nie jest sukcesem.
12. Rate limit/context/compaction/provider error muszą mieć kontrolowaną obsługę.
13. System musi posiadać durable state.
14. Nie polegaj na pamięci pojedynczego chatu Kilo.
15. Nie zapisuj sekretów w repozytorium.
16. AUTO MERGE do main pozostaje OFF.
17. Nie implementuj przyszłych etapów przed ukończeniem bieżącego.
18. Każdy krytyczny etap wymaga testów i hostile audit.
19. Raport techniczny nie zastępuje rzeczywistej weryfikacji.
20. Nie deklaruj PASS bez dowodów.

Aktualny etap:

STAGE 6.8 — AUTONOMOUS RUN CONTROLLER

Bieżący baseline:

b4a7a3bc02c68b16dcb6396d0bbba35043b3b190

Stage 6.8 pozostaje NIEZAMKNIĘTY, dopóki nie zostanie rozwiązany
problem execution safety związany z cancel/abort/timeout/workspace ownership.

Po ukończeniu Stage 6.8 przejdź kolejno:

Stage 7  → Verification Engine
Stage 8  → Gemini Manager
Stage 9  → Kilo Code Integration
Stage 10 → Autonomous Development Loop

Nie przeskakuj etapów bez osobnej decyzji.

Dla każdego etapu:

1. Zbadaj istniejący kod.
2. Zweryfikuj architekturę.
3. Przygotuj plan.
4. Implementuj tylko zakres etapu.
5. Uruchom testy.
6. Napraw błędy.
7. Uruchom pełną regresję.
8. Wykonaj hostile audit.
9. Wygeneruj raport techniczny.
10. Dopiero po spełnieniu Definition of Done zgłoś gotowość.

Nigdy nie uznawaj zadania za gotowe tylko dlatego, że kod wygląda poprawnie
lub agent zwrócił komunikat „done”.
```

---

# 42. DEFINICJA KOŃCOWEGO SUKCESU

Projekt osiąga docelowy poziom autonomicznego developmentu, gdy użytkownik może:

1. wskazać plik z planem;
2. wskazać folder docelowy;
3. określić poziom autonomii;
4. uruchomić projekt;
5. pozwolić Gemini zaplanować pracę;
6. pozwolić Operatorowi zarządzać taskami;
7. pozwolić Kilo wykonywać kod;
8. pozwolić Verification Engine sprawdzać wyniki;
9. pozwolić Operatorowi naprawiać błędy;
10. pozwolić systemowi tworzyć kolejne sesje Kilo;
11. pozwolić systemowi odzyskiwać się po awariach;
12. otrzymać końcowy raport;
13. mieć kompletną historię Git/audit;
14. zachować możliwość zatrzymania lub przejęcia kontroli.

Wtedy AI Operator będzie rzeczywistym autonomicznym środowiskiem developmentowym,
a nie tylko interfejsem wysyłającym prompty do Kilo.

---

# 43. ZASADA „SOURCE OF TRUTH”

Ten dokument jest żywym źródłem prawdy dotyczącej projektu.

Po każdej ważnej zmianie architektury należy:

1. zaktualizować wersję;
2. zaktualizować datę;
3. zaktualizować Current Verified State;
4. zaktualizować roadmap;
5. usunąć nieaktualne twierdzenia;
6. zapisać nowe twarde reguły architektoniczne;
7. zachować historię ważnych decyzji.

Nie wolno pozostawiać w dokumencie informacji, które sugerują, że nieukończony etap
jest już zamknięty.

---

# KONIEC DOKUMENTU

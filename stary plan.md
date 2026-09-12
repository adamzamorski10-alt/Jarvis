# AI OPERATOR — MASTER PROJECT CONTEXT
## Kompletny blueprint projektu dla AI programującego
**Status:** zaakceptowany plan architektury & zweryfikowana Faza 1 (Foundation MVP)  
**Data aktualizacji:** 2026-08-27  
**Wersja dokumentu:** 1.1

---

# 1. CZYM JEST TEN PROJEKT?

AI Operator to lokalny, desktopowy system osobistego asystenta AI dla Windowsa.

Nie jest to zwykły chatbot. Jest to modułowa aplikacja agentowa, która ma docelowo:

- zarządzać komputerem użytkownika;
- analizować pliki, dokumenty i foldery;
- przeszukiwać lokalne dane;
- zarządzać projektami;
- pomagać w programowaniu i analizować kod;
- korzystać z przeglądarki;
- wykonywać zadania automatycznie podczas nieobecności użytkownika;
- analizować e-maile, wiadomości i powiadomienia;
- integrować różne API i usługi;
- posiadać trwałą pamięć;
- korzystać z kilku darmowych, lokalnych modeli AI;
- posiadać kontrolowany system autonomii i uprawnień;
- w przyszłości umożliwiać dostęp z telefonu i innych komputerów.

Główna zasada projektu:

> AI nie dostaje bezpośrednio nieograniczonej kontroli nad komputerem.
> AI planuje i wybiera narzędzia, a system bezpieczeństwa decyduje,
> czy konkretna akcja może zostać wykonana automatycznie.

Projekt ma początkowo działać w 100% bezpłatnie.

---

# 2. INFORMACJE O KOMPUTERZE UŻYTKOWNIKA

Docelowa platforma początkowa:

- System: Windows
- CPU: Intel Core i7-6700K @ 4.00 GHz
- GPU: NVIDIA GeForce GTX 1080 Ti
- VRAM GPU: 11 GB
- RAM: 24 GB
- Dyski widoczne na przesłanym zrzucie:
  - Samsung SSD 960 EVO 256 GB
  - Samsung SSD 860 EVO 500 GB
  - WDC WD10EFRX / dysk 1 TB

Wniosek sprzętowy:

Komputer nadaje się do lokalnych modeli AI, ale projekt nie powinien zakładać
jednoczesnego trzymania wielu dużych modeli w VRAM.

Zalecenie:

- modele 7B–8B w Q4: podstawowy zakres pracy;
- małe modele 2B–4B: szybkie zadania;
- modele 14B+: tylko eksperymentalnie, z możliwym użyciem RAM i spadkiem szybkości;
- modele 20B–30B+: nie jako podstawowy tryb pracy na tym komputerze.

Ollama oficjalnie obsługuje Windows i karty NVIDIA, a dokumentacja podaje obsługę
NVIDIA od compute capability 5.0+. GTX 1080 Ti spełnia ten poziom.

---

# 3. PODJĘTE DECYZJE ARCHITEKTONICZNE

Użytkownik zaakceptował rozwiązanie hybrydowe:

## Rozwiązanie C — WŁASNA APLIKACJA + GOTOWE KOMPONENTY

Budujemy własny program i własny interfejs, ale nie piszemy od zera rzeczy,
które mają dobre, sprawdzone rozwiązania open source.

### Ostateczny kierunek technologiczny

```text
┌───────────────────────────────────────────────────────┐
│                    AI OPERATOR UI                     │
│              Tauri + React + TypeScript               │
├───────────────────────────────────────────────────────┤
│                  LOCAL CORE SERVICE                   │
│                 Python + FastAPI/asyncio              │
├───────────────────────────────────────────────────────┤
│ AI Router │ Agents │ Memory │ Scheduler │ Permissions │
├───────────┼────────┼────────┼───────────┼─────────────┤
│ Ollama    │ SQLite │ Tools  │ Workflows │ Audit Log   │
├───────────┴────────┴────────┴───────────┴─────────────┤
│ Files │ Windows │ Browser │ Email │ APIs │ Projects   │
└───────────────────────────────────────────────────────┘
```

---

# 4. DLACZEGO TAURI ZAMIAST ELECTRON?

Rekomendacja: Tauri + React + TypeScript.

Powody:

1. Mniejsza aplikacja i mniejsze zużycie zasobów niż typowy desktopowy stack Electron.
2. Dobra integracja z natywnymi funkcjami systemu.
3. React/TypeScript daje wygodę tworzenia nowoczesnego interfejsu.
4. Tauri ma kierunek desktop + mobile, co jest korzystne dla przyszłego dostępu
   z telefonu.
5. Backend może pozostać niezależnym lokalnym serwisem Python.

Nie oznacza to, że Rust ma być używany do całej logiki projektu.
Rust/Tauri odpowiada przede wszystkim za desktop shell i bezpieczne funkcje systemowe.
Główna inteligencja i agent runtime będą w Pythonie.

---

# 5. REKOMENDOWANY STACK

## Frontend

- Tauri
- React
- TypeScript
- Vite
- nowoczesna biblioteka komponentów UI
- własny system design tokens

## Desktop / native layer

- Tauri v2
- Rust tylko tam, gdzie jest potrzebny
- capability/permission system Tauri
- Python jako lokalny sidecar/service w wersji produkcyjnej

## AI Core

- Python
- FastAPI
- asyncio
- Pydantic
- Ollama API

## Lokalna AI

- Ollama

## Baza danych

Etap początkowy:

- SQLite
- FTS5 do szybkiego wyszukiwania tekstowego

Później:

- SQLite jako główna lokalna baza
- wektorowe wyszukiwanie przez lokalny indeks / rozszerzenie wektorowe

Nie należy od razu wprowadzać PostgreSQL. Dla jednego lokalnego użytkownika SQLite jest
znacznie prostszy. Migracja do PostgreSQL ma sens dopiero przy wielu urządzeniach lub
serwerowej synchronizacji.

## Browser Automation

- Playwright
- rekomendowana implementacja na tym komputerze: Node.js/TypeScript worker
  komunikujący się z Python Core przez lokalny kontrakt API/IPC

Uwaga: ponieważ środowisko użytkownika wygląda na Windows 10, nie należy zakładać
Playwright Python jako jedynej ścieżki wykonawczej bez wcześniejszego testu zgodności.
Browser automation pozostaje niezależnym modułem, a Python Core orkiestruje zadania.

## System automatyzacji

Początkowo:

- własny scheduler w Pythonie
- Windows Task Scheduler jako integracja systemowa

Opcjonalnie później:

- n8n self-hosted jako dodatkowy silnik workflowów

Nie należy uzależniać podstawowej architektury od n8n.

## Integracje

- REST APIs
- OAuth 2.0
- MCP — Model Context Protocol
- własne Tool Adapters

---

# 6. LOKALNE MODELE AI — REKOMENDACJA

## Zasada działania

Nie używamy jednego modelu do wszystkiego.

Program posiada AI Router:

```text
User Request
     │
     ▼
┌─────────────┐
│ AI ROUTER   │
└──────┬──────┘
       │
 ┌─────┼─────────────┬───────────────┬──────────────┐
 ▼     ▼             ▼               ▼              ▼
Fast  General       Code           Vision        Embedding
```

Router wybiera model na podstawie:

- typu zadania;
- poziomu trudności;
- potrzeby widzenia obrazu;
- potrzeby użycia narzędzi;
- dostępnych zasobów GPU/RAM;
- ustawień użytkownika.

## Model 1 — podstawowy

### Qwen3 8B

Zastosowanie:

- rozmowa;
- planowanie;
- klasyfikacja;
- analiza wiadomości;
- podejmowanie decyzji;
- wybieranie narzędzi;
- ogólne zadania agenta.

Rekomendacja startowa:

- wariant około 8B;
- standardowa kwantyzacja dostosowana do 11 GB VRAM;
- ładowanie jednego głównego modelu na raz.

## Model 2 — programowanie

### Qwen2.5-Coder 7B

Zastosowanie:

- analiza kodu;
- generowanie kodu;
- naprawianie błędów;
- praca z repozytoriami;
- analiza struktury projektu.

Rozmiar wersji 7B dostępnej w bibliotece Ollama to około 4.7 GB.

## Model 3 — analiza ekranu i obrazów

### Qwen3-VL 8B

Zastosowanie:

- analiza screenshotów;
- OCR;
- rozpoznawanie elementów interfejsu;
- pomoc przy GUI automation;
- analiza dokumentów graficznych.

Wersja Q4 8B ma około 6.1 GB.

Nie uruchamiać równolegle z innymi dużymi modelami bez potrzeby.

## Model 4 — pamięć semantyczna

### embeddinggemma lub nomic-embed-text-v2-moe

Zastosowanie:

- wyszukiwanie semantyczne;
- pamięć;
- podobieństwo dokumentów;
- wyszukiwanie informacji w projektach.

Dla polskiego i wielu języków warto przetestować model wielojęzyczny.
Wybór ostateczny powinien zostać potwierdzony benchmarkiem na rzeczywistych danych użytkownika.

## Ważna zasada zarządzania VRAM

AI Model Manager:

1. sprawdza dostępny VRAM;
2. wybiera model;
3. ładuje model tylko gdy jest potrzebny;
4. po okresie bezczynności może go zwolnić;
5. nie dopuszcza domyślnie do ładowania kilku dużych modeli jednocześnie.

---

# 7. GŁÓWNA ARCHITEKTURA AGENTÓW

Nie tworzymy wielu niezależnych agentów, które mogą wykonywać dowolne działania.

Tworzymy:

```text
                    MASTER ORCHESTRATOR
                           │
        ┌──────────────────┼───────────────────┐
        ▼                  ▼                   ▼
   Task Planner       Tool Router         Memory Manager
        │                  │                   │
        ├──────────────┬───┴───────┬──────────┤
        ▼              ▼           ▼          ▼
    File Agent    Browser Agent  Code Agent  Project Agent
        │              │           │          │
        └──────────────┴─────┬─────┴──────────┘
                             ▼
                    Permission Engine
                             │
                             ▼
                       Tool Executor
                             │
                             ▼
                         Audit Log
```

## Najważniejsza zasada

Model AI NIE wykonuje bezpośrednio:

- PowerShell;
- usuwania plików;
- wysyłania maili;
- klikania w przeglądarce;
- wyłączania systemu.

Model może jedynie zaproponować ustrukturyzowane wywołanie narzędzia.

Przykład:

```json
{
  "tool": "files.move",
  "arguments": {
    "source": "C:\\Users\\User\\Downloads\\plik.pdf",
    "destination": "C:\\Users\\User\\Documents\\Faktury\\plik.pdf"
  },
  "reason": "Dokument został sklasyfikowany jako faktura."
}
```

Następnie:

1. Permission Engine analizuje akcję.
2. Policy Engine określa wymagany poziom zgody.
3. Jeżeli potrzebna jest zgoda — tworzone jest Approval Request.
4. Jeżeli akcja jest dozwolona — Tool Executor wykonuje ją.
5. Wynik trafia do Audit Log.
6. AI otrzymuje wynik wykonania.

---

# 8. SYSTEM AUTONOMII — KLUCZOWA FUNKCJA

Użytkownik chce, aby program mógł działać w 100% autonomicznie podczas jego nieobecności,
ale jednocześnie aby miał możliwość kontrolowania zakresu autonomii.

Dlatego tworzymy SYSTEM POLITYK AUTONOMII.

## Tryby globalne

### TRYB 1 — MANUALNY

AI:

- analizuje;
- planuje;
- proponuje.

Nie wykonuje działań bez potwierdzenia.

### TRYB 2 — NADZOROWANY

AI może wykonywać akcje niskiego ryzyka.
Średnie i wysokie ryzyko wymagają zgody.

### TRYB 3 — AUTONOMIA KONTROLOWANA

AI może wykonywać większość wcześniej dozwolonych działań.
Działania krytyczne nadal wymagają zgody.

### TRYB 4 — PEŁNA AUTONOMIA W GRANICACH POLITYK

AI może pracować bez obecności użytkownika,
ale nadal obowiązują twarde reguły bezpieczeństwa.

"100% możliwości" NIE oznacza braku zasad.
Oznacza możliwość samodzielnego działania w granicach zdefiniowanych przez użytkownika.

## Poziomy ryzyka

### GREEN

Przykłady:

- odczyt pliku z dozwolonego folderu;
- wyszukiwanie informacji;
- analiza projektu;
- tworzenie raportu;
- tworzenie lokalnego pliku roboczego.

Może być wykonywane automatycznie.

### YELLOW

Przykłady:

- edycja istniejącego pliku;
- zmiana statusu zadania;
- logowanie do zaufanej usługi;
- uruchomienie aplikacji;
- zmiana niekrytycznej konfiguracji.

Zależne od polityki.

### ORANGE

Przykłady:

- wysłanie wiadomości;
- push do zdalnego repozytorium;
- instalacja pakietu;
- edycja ważnych plików projektu.

Zwykle wymaga zatwierdzenia lub wcześniej zdefiniowanej reguły.

### RED

Przykłady:

- trwałe usuwanie danych;
- formatowanie dysku;
- zmiana ustawień bezpieczeństwa;
- eksport sekretów;
- wykonanie nieznanego kodu;
- zmiana haseł;
- transakcje finansowe.

Zawsze wymaga bezpośredniej zgody.

---

# 9. APPROVAL CENTER — UŻYTKOWNIK NIE MUSI WSZYSTKIEGO KONTROLOWAĆ RĘCZNIE

Program będzie posiadał panel:

## Centrum decyzji

Przykład:

```text
AI chce wykonać:
Przenieść 47 plików z Downloads do odpowiednich folderów.

Ryzyko: NISKIE

[Zezwól raz]
[Zezwalaj zawsze dla tego typu działania]
[Zezwól tylko w tym folderze]
[Odrzuć]
[Zmień zasady]
```

Przykład dla maila:

```text
AI przygotował 3 odpowiedzi.

[Wyślij wszystkie]
[Otwórz do sprawdzenia]
[Zezwól automatycznie dla tego kontaktu]
[Odrzuć]
```

Dzięki temu użytkownik może stopniowo zwiększać autonomię bez ciągłego ręcznego zarządzania.

---

# 10. SYSTEM PAMIĘCI

Pamięć musi być podzielona.

## A. Session Memory

Kontekst aktualnej rozmowy i aktualnego zadania.

## B. User Preferences

Trwałe preferencje użytkownika.

Przykłady:

- sposób organizowania projektów;
- domyślne foldery;
- preferowany styl odpowiedzi;
- zaufane aplikacje;
- zasady autonomii.

## C. Project Memory

Każdy projekt ma własną pamięć:

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

## D. Semantic Memory

Wyszukiwanie znaczeniowe w:

- dokumentach;
- projektach;
- notatkach;
- mailach;
- historii działań.

## E. Episodic Memory

Historia ważnych zdarzeń:

- co AI zrobiło;
- dlaczego;
- jaki był wynik;
- co się nie udało;
- jakie reguły zostały zastosowane.

## F. Secrets

Sekrety NIE trafiają do zwykłej pamięci AI.

API keys, tokeny i hasła:

- są przechowywane osobno;
- nie są automatycznie przekazywane do modelu;
- model otrzymuje tylko możliwość użycia narzędzia.

---

# 11. MODUŁY PROGRAMU

## 11.1 Dashboard

Główny ekran.

Pokazuje:

- aktualnie działające zadania;
- status agentów;
- ważne powiadomienia;
- wymagające decyzje;
- aktywne automatyzacje;
- ostatnie działania;
- stan AI i zasobów komputera.

## 11.2 Assistant

Główny interfejs rozmowy z AI.

Obsługuje:

- zwykłą rozmowę;
- zadania wieloetapowe;
- planowanie;
- narzędzia;
- załączniki;
- historię;
- wybór trybu autonomii.

## 11.3 Tasks

Lista zadań:

- ręcznych;
- automatycznych;
- zaplanowanych;
- oczekujących;
- wykonanych;
- zakończonych błędem.

## 11.4 Agents

Panel pokazujący:

- który agent pracuje;
- nad czym;
- jaki ma plan;
- jakie narzędzia wywołał;
- czy oczekuje na zgodę.

## 11.5 Files

Funkcje:

- indeksowanie wybranych folderów;
- wyszukiwanie;
- wyszukiwanie semantyczne;
- analiza dokumentów;
- organizowanie plików;
- wykrywanie duplikatów;
- podgląd zmian przed wykonaniem.

## 11.6 Projects

Każdy projekt posiada:

- Overview;
- Tasks;
- Files;
- Memory;
- Architecture;
- Decisions;
- Bugs;
- AI Activity;
- Git;
- Automations.

## 11.7 Browser

Funkcje:

- wyszukiwanie;
- analiza stron;
- automatyczne workflow;
- historia działań;
- zarządzanie sesjami.

## 11.8 Messages

Docelowo:

- Gmail;
- Outlook;
- inne integracje.

Funkcje:

- klasyfikacja;
- podsumowanie;
- wykrywanie pilności;
- przypisywanie do projektów;
- przygotowywanie odpowiedzi;
- tworzenie zadań.

## 11.9 Automations

Przykład:

```text
TRIGGER:
Codziennie 23:00

ACTION:
Sprawdź nowe wiadomości
→ sklasyfikuj
→ utwórz zadania
→ przygotuj raport
→ wyślij lokalne powiadomienie
```

## 11.10 Approval Center

Wszystkie akcje oczekujące na decyzję użytkownika.

## 11.11 Activity / Audit

Pełna historia:

- kto/który agent;
- kiedy;
- co zrobił;
- jakie narzędzie;
- jakie parametry;
- wynik;
- błąd;
- zastosowana polityka.

## 11.12 Settings

Między innymi:

- modele AI;
- VRAM;
- foldery;
- autonomia;
- integracje;
- API;
- powiadomienia;
- harmonogram;
- backup;
- bezpieczeństwo.

---

# 12. FILE AGENT — PIERWSZY PRAWDZIWY AGENT

To powinien być pierwszy agent funkcjonalny.

Dlaczego?

- jest użyteczny;
- nie wymaga od razu kont zewnętrznych;
- pozwala zbudować system narzędzi;
- pozwala zbudować permissions;
- pozwala zbudować audit log;
- pozwala przetestować autonomię.

Pierwsze narzędzia:

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

WAŻNE:

Pierwsza wersja nie może wykonywać trwałego delete.
Usuwanie ma trafiać do kosza, o ile system na to pozwala.

---

# 13. BROWSER AGENT

Rekomendacja: Playwright.

Hierarchia sterowania:

## Poziom 1 — API

Najbardziej niezawodny.

## Poziom 2 — Browser automation

Playwright.

## Poziom 3 — Vision / GUI

Analiza ekranu i sterowanie GUI.

GUI automation jest fallbackiem, nie podstawową metodą.

---

# 14. COMPUTER / WINDOWS AGENT

Narzędzia systemowe muszą być małe i wyspecjalizowane.

ZŁE:

```text
run_any_command(command)
```

LEPSZE:

```text
computer.open_application(name)
computer.close_application(name)
computer.get_running_processes()
computer.get_system_status()
computer.lock_screen()
```

Polecenia PowerShell/CMD:

- nie powinny być domyślnym narzędziem;
- powinny mieć osobny poziom ryzyka;
- powinny być logowane;
- powinny mieć allowlist lub sandbox.

---

# 15. CODING AGENT

Cykl pracy:

```text
REQUEST
  ↓
ANALYZE PROJECT
  ↓
CREATE PLAN
  ↓
CREATE / SWITCH GIT BRANCH
  ↓
EDIT FILES
  ↓
RUN FORMATTERS / LINTERS
  ↓
RUN TESTS
  ↓
ANALYZE ERRORS
  ↓
FIX
  ↓
RETEST
  ↓
REPORT
```

Zasady:

- nigdy nie edytować wielu plików bez planu;
- przed ryzykowną zmianą stworzyć punkt powrotu;
- używać Git;
- pokazywać diff;
- automatycznie wykonywać testy;
- nie oznaczać zadania jako sukces bez sprawdzenia wyniku.

---

# 16. PROJECT AGENT

Projekt powinien być obiektem pierwszej klasy.

Minimalne tabele:

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

Przykład:

```text
Project: Fortnite App

Current Status:
System resale został ostatnio zmodyfikowany.

Known Issues:
- ...

Recent Changes:
- ...

Next Suggested Actions:
1. ...
2. ...
```

---

# 17. SCHEDULER I PRACA POD NIEOBECNOŚĆ UŻYTKOWNIKA

Każde zadanie tła ma strukturę:

```text
TRIGGER
  ↓
TASK DEFINITION
  ↓
PLAN
  ↓
PERMISSION CHECK
  ↓
EXECUTION
  ↓
VALIDATION
  ↓
AUDIT LOG
  ↓
NOTIFICATION
```

Typy triggerów:

- czas;
- harmonogram;
- uruchomienie komputera;
- zamknięcie aplikacji;
- nowy plik;
- nowy email;
- webhook;
- zmiana projektu;
- zakończenie innego zadania.

Przykładowe automatyzacje:

1. Codzienny raport.
2. Organizowanie Downloads.
3. Analiza nowych plików.
4. Sprawdzanie projektów.
5. Automatyczne testy.
6. Przypominanie o zadaniach.
7. Analiza ważnych wiadomości.

---

# 18. MCP I SYSTEM NARZĘDZI

MCP ma być obsługiwane jako dodatkowy standard integracji.

Własne narzędzia powinny jednak mieć wewnętrzny jednolity kontrakt.

Przykładowy Tool Manifest:

```json
{
  "id": "files.move",
  "name": "Move file",
  "risk": "yellow",
  "requires_confirmation": false,
  "input_schema": {},
  "output_schema": {}
}
```

Tool Registry powinien wiedzieć:

- czy narzędzie jest dostępne;
- jakie ma parametry;
- jaki ma poziom ryzyka;
- czy jest lokalne;
- czy wymaga internetu;
- czy wymaga zgody;
- czy jest idempotentne;
- czy można je cofnąć.

---

# 19. BAZA DANYCH — MINIMALNY SCHEMAT

Pierwsza wersja:

```text
users
settings
conversations
messages
tasks
task_runs
agents
agent_runs
tools
tool_runs
approvals
projects
project_tasks
project_notes
automations
automation_runs
notifications
audit_events
memories
```

Najważniejsza tabela:

## audit_events

```text
id
timestamp
task_id
agent_id
tool_id
action
input_summary
risk_level
policy_result
approval_id
result
error
```

---

# 20. INTERFEJS — REKOMENDOWANY UKŁAD

Nowoczesny, ciemny interfejs desktopowy.

```text
┌───────────────────────────────────────────────────────────────┐
│ AI OPERATOR                                      ● LOCAL AI   │
├───────────────┬───────────────────────────────────────────────┤
│ Dashboard     │                                               │
│ Assistant     │              GŁÓWNY OBSZAR                    │
│ Tasks         │                                               │
│ Agents        │                                               │
│ Files         │     Kontekst aktualnego modułu                │
│ Projects      │                                               │
│ Browser       │                                               │
│ Messages      │                                               │
│ Automations   │                                               │
│ Approvals     │                                               │
│ Activity      │                                               │
│ Settings      │                                               │
│               │                                               │
└───────────────┴───────────────────────────────────────────────┘
```

Na dole zawsze:

- status AI;
- aktywny model;
- VRAM/RAM;
- liczba aktywnych zadań;
- tryb autonomii.

Priorytet UX:

1. przejrzystość;
2. widoczność tego, co robi AI;
3. łatwe zatrzymanie zadania;
4. łatwa zmiana poziomu autonomii;
5. brak przesadnie technicznego interfejsu dla codziennego użycia.

---

# 21. PLAN REALIZACJI

## FAZA 0 — SPECYFIKACJA

Cel:

Nie pisać funkcji przed zdefiniowaniem fundamentów.

Do zrobienia:

- [ ] utworzyć repozytorium Git;
- [ ] zapisać ten dokument jako główny kontekst projektu;
- [ ] utworzyć dokument ARCHITECTURE.md;
- [ ] utworzyć dokument SECURITY.md;
- [ ] utworzyć dokument ROADMAP.md;
- [ ] zdefiniować nazwy modułów;
- [ ] ustalić kontrakty API.

## FAZA 1 — FOUNDATION MVP

Cel:

Uruchomiona aplikacja desktopowa.

Zakres:

- [x] Tauri;
- [x] React;
- [x] TypeScript;
- [x] Python local core;
- [x] FastAPI health endpoint;
- [x] SQLite;
- [x] podstawowy UI shell;
- [x] sidebar;
- [x] Dashboard;
- [x] Activity panel;
- [x] Settings;
- [x] logging.

Definition of Done:

Aplikacja uruchamia się jednym poleceniem i potrafi komunikować się z lokalnym Python Core.

## FAZA 2 — LOCAL AI

Zakres:

- [ ] instalacja Ollama;
- [ ] wykrywanie Ollama;
- [ ] wykrywanie modeli;
- [ ] chat;
- [ ] streaming odpowiedzi;
- [ ] wybór modelu;
- [ ] AI status;
- [ ] obsługa błędów.

Definition of Done:

Użytkownik może rozmawiać z lokalnym modelem przez desktopową aplikację.

## FAZA 3 — TOOL SYSTEM

Zakres:

- [ ] Tool Registry;
- [ ] JSON schema;
- [ ] Tool Executor;
- [ ] Permission Engine;
- [ ] Audit Log;
- [ ] Approval Center.

Definition of Done:

AI może wywołać testowe narzędzie, ale akcja przechodzi przez kontrolę polityk.

## FAZA 4 — FILE AGENT

Zakres:

- [ ] Files UI;
- [ ] folder permissions;
- [ ] wyszukiwanie;
- [ ] odczyt metadanych;
- [ ] bezpieczne operacje;
- [ ] kosz zamiast permanent delete.

Definition of Done:

AI może wykonać zadanie typu:
"Znajdź wszystkie PDF-y w wybranym folderze i pogrupuj je według typu."

## FAZA 5 — TASK ENGINE

Zakres:

- [ ] kolejka zadań;
- [ ] statusy;
- [ ] retry;
- [ ] anulowanie;
- [ ] task plans;
- [ ] validation.

## FAZA 6 — PROJECT SYSTEM

Zakres:

- [ ] tworzenie projektów;
- [ ] pamięć projektu;
- [ ] zadania;
- [ ] decyzje;
- [ ] analiza repozytorium;
- [ ] Git integration.

## FAZA 7 — CODING AGENT

Zakres:

- [ ] analiza kodu;
- [ ] plan zmian;
- [ ] edycja;
- [ ] diff;
- [ ] testy;
- [ ] raport.

## FAZA 8 — BROWSER AGENT

Zakres:

- [ ] Playwright;
- [ ] profile/sesje;
- [ ] structured actions;
- [ ] browser audit;
- [ ] approval policies.

## FAZA 9 — AUTOMATIONS

Zakres:

- [ ] scheduler;
- [ ] triggers;
- [ ] background tasks;
- [ ] notifications;
- [ ] failure recovery.

## FAZA 10 — EMAIL / MESSAGE INTEGRATIONS

Zakres:

- [ ] OAuth;
- [ ] read-only first;
- [ ] classification;
- [ ] summaries;
- [ ] drafts;
- [ ] controlled sending.

## FAZA 11 — GUI / VISION AGENT

Zakres:

- [ ] screenshot capture;
- [ ] Qwen3-VL;
- [ ] screen understanding;
- [ ] safe coordinate/action layer.

NIE zaczynać od tej fazy.

## FAZA 12 — REMOTE ACCESS

Dopiero po stabilizacji lokalnej aplikacji.

Możliwości:

- telefon jako zdalny panel;
- inny komputer;
- synchronizacja;
- bezpieczny tunnel/VPN;
- powiadomienia mobilne.

---

# 22. CO ZROBIĆ NA SAMYM POCZĄTKU?

To jest rekomendowana kolejność.

## KROK 1 — NIE BUDOWAĆ JESZCZE WSZYSTKICH AGENTÓW

Pierwszy cel:

> Stworzyć stabilny fundament AI Operator.

## KROK 2 — Utworzyć prywatne repozytorium

Repozytorium:

```text
ai-operator
```

Branch strategy:

```text
main
develop
feature/*
fix/*
```

## KROK 3 — ZAINSTALOWAĆ ŚRODOWISKO

Potrzebne:

- Git;
- Node.js LTS;
- pnpm;
- Python 3.12 lub nowszy stabilny;
- Rust;
- Microsoft C++ Build Tools;
- Ollama;
- VS Code lub inny edytor.

## KROK 4 — UTWORZYĆ MONOREPO

Rekomendowana struktura:

```text
ai-operator/
├── apps/
│   └── desktop/
│       ├── src/
│       ├── src-tauri/
│       └── package.json
│
├── services/
│   └── core/
│       ├── app/
│       │   ├── api/
│       │   ├── agents/
│       │   ├── ai/
│       │   ├── tools/
│       │   ├── permissions/
│       │   ├── tasks/
│       │   ├── memory/
│       │   ├── projects/
│       │   ├── db/
│       │   └── main.py
│       ├── tests/
│       └── pyproject.toml
│
├── packages/
│   └── contracts/
│
├── docs/
│   ├── MASTER_PROJECT_CONTEXT.md
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   └── ROADMAP.md
│
└── README.md
```

## KROK 5 — ZROBIĆ "VERTICAL SLICE"

Pierwszy kompletny przepływ:

```text
User writes:
"Przetestuj narzędzie systemowe"

        ↓

Desktop UI

        ↓

Python Core API

        ↓

AI / test task

        ↓

Tool Registry

        ↓

Permission Check

        ↓

Tool Executor

        ↓

Audit Log

        ↓

Result in UI
```

Dopiero gdy ten przepływ działa, przechodzimy dalej.

---

# 23. NAJLEPSZE PIERWSZE MVP

Pierwsza wersja powinna mieć tylko:

1. Desktop app.
2. Local AI chat.
3. AI status.
4. Tool Registry.
5. Approval Center.
6. Audit Log.
7. Jeden bezpieczny File Agent.
8. Jeden prosty background task.

Przykładowy test końcowy MVP:

> "Przeszukaj wybrany przeze mnie folder, znajdź wszystkie pliki tekstowe zawierające słowo 'Fortnite', stwórz raport i zapisz go w folderze Reports."

Program powinien:

1. utworzyć plan;
2. wybrać tools;
3. wykonać search;
4. przeczytać wyniki;
5. stworzyć raport;
6. sprawdzić politykę zapisu;
7. zapisać raport;
8. dodać wszystkie akcje do Activity/Audit;
9. pokazać wynik użytkownikowi.

Jeżeli to działa stabilnie, architektura jest poprawna.

---

# 24. TESTY — OBOWIĄZKOWE

Każda funkcja musi mieć odpowiedni poziom testów.

## Unit tests

Dla:

- permissions;
- risk calculation;
- task state;
- tool validation;
- database logic.

## Integration tests

Dla:

- API;
- Tool Registry;
- SQLite;
- Ollama adapter.

## End-to-end

Dla:

- desktop UI;
- całego przepływu zadania;
- approvals.

## Browser tests

Playwright.

## Manual acceptance tests

Scenariusze rzeczywiste użytkownika.

---

# 25. DEFINICJA "GOTOWEJ FUNKCJI"

AI programujące nie może uznawać funkcji za gotową tylko dlatego, że kod się kompiluje.

Każda funkcja jest DONE dopiero gdy:

- [ ] została zaimplementowana;
- [ ] nie łamie istniejącej architektury;
- [ ] ma obsługę błędów;
- [ ] ma testy odpowiednie do ryzyka;
- [ ] została przetestowana;
- [ ] UI pokazuje stan działania;
- [ ] akcje są logowane, jeśli mają skutki;
- [ ] nie ma niepotrzebnego mocka zamiast realnej implementacji.

---

# 26. ZASADY DLA AI, KTÓRE BĘDZIE PISAŁO PROGRAM

AI programujące ma przestrzegać:

1. Najpierw analizuj istniejący kod.
2. Nie przepisuj całych modułów bez potrzeby.
3. Nie usuwaj istniejących funkcji bez uzasadnienia.
4. Najpierw przygotuj plan zmian dla większych funkcji.
5. Implementuj małymi etapami.
6. Po implementacji uruchamiaj testy.
7. Po błędzie analizuj log, nie zgaduj.
8. Nie deklaruj sukcesu bez weryfikacji.
9. Nie dawaj modelowi AI nieograniczonego shell access.
10. Wszystkie skuteczne akcje muszą być możliwe do audytu.
11. Preferuj API nad GUI automation.
12. Preferuj wyspecjalizowane narzędzia nad generic shell commands.
13. Nowe integracje muszą przechodzić przez permission model.
14. Nie zapisuj sekretów w kodzie ani zwykłej bazie.
15. Zachowuj kompatybilność wsteczną.
16. Używaj Git przed dużymi zmianami.
17. Nie instaluj przypadkowych pakietów bez uzasadnienia.
18. Utrzymuj kod modułowy.
19. Nie twórz architektury mikroserwisowej bez potrzeby.
20. Lokalność i prywatność są priorytetem.

---

# 27. MASTER PROMPT DLA AI PROGRAMUJĄCEGO

Poniższy prompt można przekazać AI razem z tym dokumentem:

```text
Jesteś głównym programistą projektu AI Operator.

Przeczytaj dokładnie cały dokument MASTER_PROJECT_CONTEXT.md i traktuj go
jako aktualne źródło prawdy dotyczące projektu.

Projekt jest lokalną aplikacją desktopową dla Windows, której celem jest
stworzenie osobistego systemu agentowego AI.

Aktualna architektura:
- Tauri + React + TypeScript: desktop UI
- Python + FastAPI + asyncio: Local Core
- Ollama: lokalne modele AI
- SQLite: lokalna baza danych
- Playwright: browser automation
- MCP + własny Tool Registry: integracje
- Permission Engine + Approval Center + Audit Log: bezpieczeństwo

Nie implementuj całego projektu naraz.

Pracuj etapami zgodnie z roadmapą.

Dla każdego etapu:
1. przeanalizuj istniejący kod;
2. przedstaw krótki plan zmian;
3. wskaż pliki do utworzenia lub modyfikacji;
4. zaimplementuj funkcję;
5. uruchom aplikację lub testy;
6. przeanalizuj błędy;
7. popraw błędy;
8. powtórz testy;
9. dopiero wtedy uznaj etap za zakończony.

Nigdy nie uznawaj zadania za gotowe bez rzeczywistej weryfikacji.

Nie zastępuj realnej implementacji atrapami, jeśli funkcję można zaimplementować.

Nie dawaj modelowi AI bezpośredniego, nieograniczonego dostępu do systemu.
Wszystkie działania o skutkach zewnętrznych muszą przechodzić przez Tool Registry,
Permission Engine i Audit Log.

Aktualny cel:
Zacząć od FAZY 1 — FOUNDATION MVP.

Najpierw przygotuj analizę struktury projektu i dokładny plan implementacji FAZY 1.
Nie implementuj kolejnych faz, dopóki FAZA 1 nie zostanie uruchomiona i przetestowana.
```

---

# 28. CO ROBIMY TERAZ?

## Najbliższa rekomendowana akcja

Rozpoczynamy od:

### FAZA 1 — FOUNDATION MVP

Pierwszy konkretny zakres implementacji:

- Tauri desktop app;
- React + TypeScript;
- nowoczesny UI shell;
- Python Core;
- FastAPI;
- health endpoint;
- połączenie Desktop ↔ Core;
- SQLite;
- podstawowy logging;
- Dashboard;
- Activity;
- Settings;
- pierwszy test end-to-end.

Nie dodajemy jeszcze:

- maili;
- pełnej kontroli komputera;
- GUI automation;
- przeglądarki;
- wielu agentów;
- n8n;
- zdalnego dostępu.

Najpierw fundament.

---

# 29. DŁUGOTERMINOWA WIZJA

Docelowo AI Operator ma działać jak osobisty system operacyjny dla pracy użytkownika.

Przykład:

```text
08:00
AI analizuje nowe informacje.

09:00
Użytkownik otwiera AI Operator.

Dashboard:
- 3 ważne wiadomości
- 2 projekty wymagają uwagi
- 1 automatyzacja zakończyła się błędem
- 5 zadań wykonanych w nocy

Użytkownik:
"Napraw błędy w projekcie, przygotuj odpowiedzi na ważne maile
i uporządkuj pliki pobrane wczoraj."

AI:
- tworzy plan;
- przypisuje zadania agentom;
- wykonuje bezpieczne działania;
- pyta tylko tam, gdzie wymaga tego polityka;
- testuje wyniki;
- raportuje końcowy rezultat.
```

To jest docelowa wizja projektu.

---

# 30. ŹRÓDŁA TECHNICZNE DO WERYFIKACJI

## Ollama

Windows:
https://docs.ollama.com/windows

GPU support:
https://docs.ollama.com/gpu

Tool calling:
https://docs.ollama.com/capabilities/tool-calling

Model Qwen3:
https://ollama.com/library/qwen3

Model Qwen2.5-Coder:
https://ollama.com/library/qwen2.5-coder

Model Qwen3-VL:
https://ollama.com/library/qwen3-vl

EmbeddingGemma:
https://ollama.com/library/embeddinggemma

Nomic multilingual embeddings:
https://ollama.com/library/nomic-embed-text-v2-moe

## Tauri

Main:
https://tauri.app/start/

Create project:
https://v2.tauri.app/start/create-project/

Frontend:
https://v2.tauri.app/start/frontend/

Prerequisites:
https://v2.tauri.app/start/prerequisites/

## Playwright

Main:
https://playwright.dev/

Node.js / TypeScript:
https://playwright.dev/docs/intro

Python documentation (opcjonalna integracja):
https://playwright.dev/python/docs/intro

## MCP

Tools specification:
https://modelcontextprotocol.io/specification/2025-06-18/server/tools

## n8n

Docs:
https://docs.n8n.io/

Security audit:
https://docs.n8n.io/hosting/securing/security-audit/

---

# 31. CRITICAL ARCHITECTURAL RULES (ZASADY ARCHITEKTONICZNE)

Poniższe zasady stanowią twarde wymagania projektowe wynikające ze zweryfikowanej architektury i nie mogą być łamane podczas dalszych prac:

1. **Dynamiczny port Core**:
   - Python Core zawsze uruchamia się na dynamicznym porcie przypisywanym przez system operacyjny (`TcpListener::bind("127.0.0.1:0")`).
   - Zakazane jest hardcodowanie portów (np. `8000` lub `3854`) we frontendzie i logice aplikacyjnej.

2. **Jednolite źródło prawdy dla Core URL**:
   - Wszystkie moduły frontendowe (zarówno `apiClient` / `Assistant`, jak i polling `useCoreStatus`) korzystają ze wspólnej metody `getCoreUrl()` oraz szyny powiadomień `subscribeCoreStatus`.
   - Każde udane zapytanie API w `apiClient` (np. odpowiedź z Assistant) natychmiast rozgłasza stan `CORE ONLINE` dla całego interfejsu.

3. **Separacja błędów aplikacyjnych HTTP od braku połączenia**:
   - Odpowiedź HTTP z Core (w tym statusy 401, 409, 429, 500, 503) oznacza, że serwer Core działa i odpowiada. Taki błąd NIE MOŻE ustawiać statusu aplikacji na `CORE OFFLINE`.
   - Klasa `CoreOfflineError` jest zastrzeżona wyłącznie dla rzeczywistego braku połączenia sieciowego po wykorzystaniu próby auto-retry.

4. **Bezpieczne logowanie bez wycieku sekretów**:
   - Wszystkie wpisy logowania diagnostycznego (`[Assistant API]` oraz `[Core Status]`) muszą automatycznie maskować klucze API (`openrouter_api_key`, `kimi_api_key`), nagłówki `Authorization` oraz tokeny, zastępując wartości ciągiem `"***"`.

5. **Niezależność produkcyjnego EXE od Node/Vite**:
   - Produkcyjny pakiet Tauri (`tauri-app.exe`) budowany poleceniem `npm run tauri -- build` ładuje statyczne pliki z `frontendDist: "../dist"` (`http://tauri.localhost/`) i działa bez serwera deweloperskiego Node.js / Vite.
   - Zakazane jest nadpisywanie produkcyjnej binarki bezpośrednim poleceniem `cargo build --release` bez zmiennych CLI Tauri.

6. **Deduplikacja, obsługa Retry i `client_message_id`**:
   - Wszystkie wiadomości wysyłane z Assistant posiadają unikalne `client_message_id` zapobiegające powielaniu wpisów.
   - Nowe wysłanie identycznej treści przez użytkownika jako osobnej wiadomości musi być zawsze możliwe.

7. **Automatyczne migracje bazy danych SQLite**:
   - Procedura `init_db()` w backendzie automatycznie przeprowadza migracje SQLite (`ALTER TABLE messages ADD COLUMN client_message_id`), zapewniając kompatybilność ze starszymi lokalnymi bazami danych użytkownika.

---

# 32. ERROR HANDLING AND DIAGNOSTICS (OBSŁUGA BŁĘDÓW I DIAGNOSTYKA)

Architektura obsługi błędów rozróżnia komunikat dla użytkownika, wewnętrzny kod błędu oraz diagnostykę techniczną:

| Kod błędu (`code`) | Opis techniczny | Status HTTP | Retryable | Prezentowany komunikat dla użytkownika |
| :--- | :--- | :--- | :--- | :--- |
| `core_unreachable` | Brak połączenia z sidecarem Core na porcie dynamicznym | Network error | Tak (1x) | "The local AI Operator Core could not be reached." |
| `core_timeout` | Przekroczono limit czasu zapytania (np. 120s dla chatu) | Timeout | Tak | "AI response timed out" |
| `provider_not_configured` | Brak skonfigurowanego klucza API dla wybranego providera | HTTP 409 | Nie | "[Provider] is not configured. Please enter your API key in Settings → AI." |
| `invalid_api_key` | Nieprawidłowy lub wygasły klucz API | HTTP 401 | Nie | "Invalid API key for [Provider]. Please update key in Settings." |
| `provider_rate_limited` | Przekroczono limit zapytań u dostawcy AI | HTTP 429 | Tak | "The [Provider] service is currently rate limited." |
| `provider_quota_exceeded` | Przekroczono limit środków / kredytów u dostawcy AI | HTTP 402 | Nie | "API quota exceeded for [Provider]." |
| `model_not_found` | Wybrany model nie istnieje lub jest niedostępny | HTTP 404 | Nie | "Selected model is not available." |
| `provider_unavailable` | Usługa dostawcy AI jest niedostępna lub przeciążona | HTTP 503 | Tak | "[Provider] service is temporarily unavailable." |
| `http_500` / `internal_error` | Wewnętrzny błąd serwera Core / błąd zapytania DB | HTTP 500 | Tak | Wyświetla szczegółowy `detail` zwrócony przez backend |

---

# 33. CURRENT VERIFIED STATE (AKTUALNY ZWERYFIKOWANY STAN)

| Komponent | Status | Poziom weryfikacji | Opis i potwierdzenie weryfikacji |
| :--- | :--- | :--- | :--- |
| **Frontend Production Build** | VERIFIED | Automated & Build | `npm run build` tworzy produkcyjny bundle w `apps/desktop/dist`. 34/34 testów vitest passed. |
| **Backend Core Service** | VERIFIED | Automated & Unit | `FastAPI` + `asyncio`. 75/75 testów pytest passed. |
| **Python Sidecar Binary** | VERIFIED | Real Build | Binarka `ai-operator-core.exe` zbudowana przez PyInstaller i skopiowana do binaries. |
| **Dynamic Core Port Resolution** | VERIFIED | Real Runtime | Port przydzielany dynamicznie przez OS; przekazywany do UI komendą `get_core_url`. |
| **ApiClient Auto-Retry** | VERIFIED | Automated & Runtime | Auto-retry ze świeżym `get_core_url` po błędzie `TypeError`. |
| **Single Source of Truth Status** | VERIFIED | Automated & Real EXE | Pasek statusu oraz Assistant dzielą ten sam stan `CORE ONLINE`. |
| **Assistant & OpenRouter Integration** | VERIFIED | End-to-End Chat | Wysłanie wiadomości i odebranie realnej odpowiedzi AI przez Assistant. |
| **SQLite Auto-Migration** | VERIFIED | Automated & Real DB | Automatyczne dodawanie kolumny `client_message_id` w `init_db()`. |
| **Production EXE Execution** | VERIFIED | Real Launch | Świeży `tauri-app.exe` (SHA256: `2CF9E57A...`) przetestowany bez Node/Vite w tle. |
| **Core Status Indicator** | VERIFIED | Real Launch & Chat | Status `CORE ONLINE` pozostaje spójny podczas rozmowy z Assistant. |

---

# 34. KNOWN LIMITATIONS AND NEXT STEPS (OGRANICZENIA I KOLEJNE KROKI)

### 1. Rozwiązane problemy (Naprawione)
- Usunięto fałszywy status `CORE OFFLINE` występujący podczas prawidłowego działania Assistant.
- Naprawiono błąd braku kolumny `client_message_id` w SQLite za pomocą automatycznej migracji w `init_db()`.
- Dodano maskowanie sekretów w logach diagnostycznych `[Assistant API]` oraz `[Core Status]`.
- Naprawiono konfigurację `tauri.conf.json` (`url: "index.html"`) zapobiegającą ładowaniu dev-url w trybie produkcyjnym.

### 2. Aktualne ograniczenia
- **OpenRouter Free Router Limits**: Zewnętrzny model `openrouter/free` przy dużym obciążeniu serwerów OpenRouter może zwracać status HTTP 429/503.
- **Dostępność Klucza API**: Dla płatnych/chmurowych providerów wymagane jest podanie własnego klucza API w Settings → AI.

### 3. Następne kroki w projekcie (Roadmap)
- **Faza 2 (Local AI)**: Wykrywanie lokalnej instancji Ollama oraz automatyczne pobieranie/zarządzanie lokalnymi modelami GPU.
- **Faza 3 (Tool System)**: Tool Registry, silnik uprawnień Permission Engine oraz panel zatwierdzeń Approval Center.
- **Faza 4 (File Agent)**: Moduł zarządzania plikami z bezpiecznym przenoszeniem do kosza.

---

# KONIEC DOKUMENTU

Ten dokument jest żywym źródłem prawdy dla projektu.
Przy każdej ważnej zmianie architektury należy aktualizować jego wersję,
datę i odpowiednie decyzje.

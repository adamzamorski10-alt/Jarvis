# AI OPERATOR — DOCELOWA WIZJA PRODUKTU, MOŻLIWOŚCI I KIERUNEK ROZWOJU

**Document status:** Long-term product and architecture direction
**Date:** 2026-09-12

---

# 1. Cel projektu

AI Operator ma docelowo być prywatnym, osobistym i możliwie autonomicznym systemem AI, który stanowi centralny interfejs użytkownika do jego cyfrowego środowiska.

Nie ma być wyłącznie chatbotem ani prostym asystentem odpowiadającym na pytania.

Docelowo AI Operator ma:

* rozumieć polecenia głosowe i tekstowe,
* prowadzić naturalną rozmowę,
* wykonywać zadania w imieniu użytkownika,
* korzystać z Internetu i zewnętrznych usług,
* zarządzać dozwolonymi aplikacjami,
* korzystać z plików i danych użytkownika,
* zarządzać zadaniami, wydarzeniami, obserwacjami i przypomnieniami,
* korzystać z wielu modeli AI i dostawców,
* samodzielnie wybierać odpowiedni model do zadania,
* pamiętać istotny kontekst użytkownika zgodnie z polityką prywatności,
* wykrywać brakujące informacje i dopytywać użytkownika,
* wykrywać sytuacje wymagające zgody,
* samodzielnie proponować usprawnienia,
* monitorować wybrane przez użytkownika informacje,
* informować użytkownika o istotnych zdarzeniach,
* pomagać użytkownikowi w organizacji pracy i życia cyfrowego,
* a docelowo również zarządzać komputerem poprzez kontrolowany Computer Use.

Głównym celem nie jest maksymalna autonomia sama w sobie.

Celem jest:

> **maksymalnie użyteczny, inteligentny i proaktywny system, który może działać samodzielnie w granicach jasno określonych przez użytkownika.**

---

# 2. Główny sposób korzystania z AI Operatora

Docelowo głównym interfejsem AI Operatora ma być **rozmowa głosowa**.

Użytkownik powinien móc uruchomić AI Operatora i prowadzić z nim naturalną rozmowę bez konieczności ciągłego korzystania z klawiatury.

Przykład:

> „Powiedz mi, co ciekawego wydarzyło się dzisiaj.”

AI Operator powinien:

1. zrozumieć polecenie,
2. zdecydować, że potrzebny jest aktualny Internet/research,
3. wyszukać informacje,
4. ocenić ich znaczenie,
5. uwzględnić dozwolony kontekst użytkownika,
6. wybrać najciekawsze informacje,
7. odpowiedzieć głosowo.

Tekst pozostaje pełnoprawnym alternatywnym interfejsem.

Głos i tekst powinny korzystać z tego samego centralnego systemu rozumienia intencji, planowania, pamięci, narzędzi, uprawnień i wykonywania zadań.

---

# 3. Interfejs użytkownika

AI Operator nie powinien ograniczać się do jednej zakładki rozmowy.

Docelowa aplikacja powinna posiadać wiele wyspecjalizowanych widoków, przy czym rozmowa pozostaje głównym centrum interakcji.

Przykładowe obszary:

* Chat / Voice,
* Tasks,
* Automations,
* Monitoring / Watchlists,
* Notifications,
* Memory,
* Activity / History,
* AI Models,
* AI Providers,
* Permissions,
* Approvals,
* Projects,
* Settings,
* ewentualnie inne moduły wynikające z rozwoju systemu.

Interfejs powinien być jednak podporządkowany centralnemu AI Managerowi, a nie stanowić zbioru niezależnych aplikacji.

---

# 4. Naturalna rozmowa i dopytywanie

AI Operator musi posiadać zdolność do wykrywania brakujących informacji.

Nie powinien wykonywać zadania na podstawie nieuzasadnionych założeń.

Przykład:

Użytkownik:

> „Dodaj nowy wydatek do Finory. Kupiłem dzisiaj batonika.”

Jeżeli do wykonania operacji potrzebna jest cena, AI powinno odpowiedzieć:

> „Jasne. Ile kosztował batonik?”

Po otrzymaniu ceny AI może przygotować dane na podstawie dostępnego kontekstu.

Przykładowo:

> „Dodać wydatek 4,50 zł, kategoria: jedzenie, opis: batonik, data: dzisiaj?”

AI może samodzielnie wygenerować oczywiste elementy, ale nie może wymyślać krytycznych danych.

Ta zasada powinna być uniwersalna.

AI powinno rozpoznawać:

* brakujące dane,
* niejednoznaczność,
* sprzeczne informacje,
* niewystarczającą pewność,
* operacje wymagające dodatkowego potwierdzenia,
* operacje wymagające wyższego poziomu uprawnień.

Powinno zadawać możliwie mało pytań, ale wystarczająco dużo, aby bezpiecznie i poprawnie wykonać zadanie.

---

# 5. Proaktywność

AI Operator nie powinien być wyłącznie systemem reaktywnym.

Docelowo powinien mieć możliwość samodzielnego inicjowania komunikacji z użytkownikiem, jeżeli istnieje ku temu uzasadniony powód.

Przykładowo:

> „Znalazłem promocję na iPada 11. generacji, o której wcześniej mnie prosiłeś. Cena wynosi X.”

albo:

> „Podczas analizy działania systemu znalazłem potencjalne usprawnienie. Uważam, że warto zmienić X na Y. Czy chcesz, żebym je wdrożył?”

albo:

> „Mam pomysł na nową funkcję AI Operatora. Uważam, że może znacząco poprawić sposób wykonywania X. Chcesz, żebym przedstawił propozycję?”

Proaktywność musi być kontrolowana przez politykę użytkownika.

AI nie powinno generować niepotrzebnych powiadomień ani prowadzić niekontrolowanej aktywności.

---

# 6. Samodzielne propozycje ulepszeń AI Operatora

AI Operator powinien docelowo posiadać mechanizm samodzielnej analizy własnego działania i wykrywania potencjalnych usprawnień.

Przykładowy przepływ:

```text
obserwacja działania
        ↓
wykrycie problemu / ograniczenia / okazji
        ↓
analiza możliwych rozwiązań
        ↓
ocena wartości i ryzyka
        ↓
propozycja dla użytkownika
        ↓
zgoda użytkownika
        ↓
plan
        ↓
implementacja
        ↓
testy i weryfikacja
        ↓
raport
```

Przykład:

> „Podczas analizy działania zauważyłem, że w określonych sytuacjach wykonuję niepotrzebnie dwa zapytania. Mogę zmienić ten mechanizm tak, aby wykonywać jedno. Powinno to zmniejszyć czas działania i zużycie limitów. Czy chcesz, żebym to wdrożył?”

AI może również proponować całkowicie nowe funkcje:

> „Mam pomysł na innowację AI Operatora. Mógłbym dodać system X, który robi Y. Uważam, że byłby przydatny, ponieważ Z. Czy chcesz, żebym przygotował szczegółowy plan?”

Samodzielne proponowanie zmian nie oznacza automatycznego wdrażania.

Zmiany systemu powinny nadal podlegać polityce uprawnień, ocenie ryzyka, testom, weryfikacji i odpowiednim punktom zatwierdzania.

---

# 7. Monitoring i Watchlists

Użytkownik powinien móc powiedzieć AI Operatorowi, aby przez określony czas monitorował wybraną informację.

Przykład:

> „Powiedz mi, kiedy znajdziesz promocję na iPada 11. generacji.”

AI powinien utworzyć kontrolowane zadanie monitorujące.

Przykładowy cykl:

```text
utworzenie watchlist
        ↓
okresowe sprawdzanie
        ↓
wykrycie zmiany
        ↓
ocena, czy spełnia kryteria
        ↓
powiadomienie użytkownika
```

Przykład powiadomienia:

> „Znalazłem promocję na iPada 11. generacji, tak jak mnie prosiłeś. Cena wynosi X.”

System powinien obsługiwać między innymi:

* warunki,
* częstotliwość sprawdzania,
* czas rozpoczęcia,
* czas zakończenia,
* źródła,
* priorytet,
* sposób powiadomienia,
* możliwość zatrzymania monitorowania.

---

# 8. Monitoring treści i zainteresowań

AI Operator powinien docelowo móc obserwować dozwolone źródła informacji i wykorzystywać je do wyszukiwania rzeczy potencjalnie interesujących użytkownika.

Przykład:

> „Na podstawie piosenek z playlisty X na Spotify powiedz mi, kiedy pojawi się premiera utworu lub artysty, który może mnie zainteresować.”

System może:

1. uzyskać dostęp do dozwolonych danych,
2. określić zainteresowania wynikające z danych,
3. monitorować odpowiednie źródła,
4. wykrywać nowe informacje,
5. oceniać ich trafność,
6. poinformować użytkownika.

Takie funkcje muszą działać tylko w zakresie danych i integracji, do których użytkownik udzielił odpowiednich uprawnień.

---

# 9. Proaktywne sugestie dotyczące użytkownika

AI Operator może docelowo analizować dozwolony kontekst użytkownika i samodzielnie proponować działania.

Przykład:

> „Widzę, że masz teraz około godziny wolnego. Jeżeli chcesz, możesz wykonać zaplanowany blok treningowy z EVOLVE.”

Inny przykład:

> „Korzystasz z komputera od około czterech godzin. Sugeruję krótką przerwę. Możesz zrobić spacer, rozciąganie albo wykonać krótkie ćwiczenia.”

Jeszcze inny:

> „Masz jutro bardzo dużo zaplanowanych zadań. Chcesz, żebym pomógł Ci przeorganizować dzień?”

AI Operator powinien traktować takie sugestie jako pomoc, a nie jako wymuszanie działania.

Użytkownik powinien mieć możliwość skonfigurowania:

* czy takie sugestie są aktywne,
* jak często mogą się pojawiać,
* w jakich obszarach,
* w jakich godzinach,
* jak bardzo proaktywne ma być AI.

---

# 10. Pamięć i model użytkownika

AI Operator powinien posiadać system pamięci pozwalający przechowywać użyteczny kontekst użytkownika.

Może to obejmować między innymi:

* preferencje,
* zainteresowania,
* projekty,
* cele,
* rutyny,
* wcześniejsze decyzje,
* preferowane sposoby pracy,
* historię współpracy z AI.

W przyszłości system może również wykorzystywać dane z innych urządzeń i aplikacji, ale tylko w zakresie zaakceptowanym przez użytkownika.

Nie należy projektować tego jako nieograniczonego monitorowania.

Powinien istnieć jawny model źródeł danych i uprawnień, np.:

```text
Chat history          ALLOW
Finora                ALLOW
EVOLVE                ALLOW
Wybrane aplikacje     ALLOW
Przeglądarka          ASK
Pliki                  ASK
Email                  ASK
Telefon                ASK
```

Użytkownik powinien móc zmienić te ustawienia.

---

# 11. Poziomy autonomii

AI Operator powinien posiadać co najmniej trzy poziomy autonomii.

## ASSISTED

AI często pyta użytkownika przed wykonaniem działań.

Przeznaczone dla nowych lub wrażliwych integracji.

## SMART

AI samodzielnie wykonuje działania niskiego ryzyka, natomiast pyta w przypadku:

* niepewności,
* brakujących danych,
* istotnych decyzji,
* działań kosztownych,
* działań destrukcyjnych,
* działań wymagających wyższego poziomu uprawnień.

To powinien być domyślny docelowy tryb.

## AUTONOMOUS

AI może wykonywać większą liczbę działań samodzielnie w ramach wcześniej określonych polityk.

Autonomia nie oznacza braku zabezpieczeń.

Nadal obowiązują:

* Permission Engine,
* Approval Service,
* Audit,
* Tool Execution,
* limity,
* polityki ryzyka,
* fail-closed,
* ograniczenia zakresu działania.

---

# 12. Wielu dostawców AI i wiele modeli

AI Operator powinien być niezależny od jednego dostawcy AI.

Docelowo powinien obsługiwać:

* modele lokalne,
* darmowe API,
* płatne API,
* wielu dostawców,
* wielu modeli tego samego dostawcy,
* kompatybilne własne endpointy, jeżeli architektura i bezpieczeństwo na to pozwalają.

Przykładowe źródła:

* Ollama,
* OpenRouter,
* oficjalne API różnych dostawców,
* inne kompatybilne providery.

Użytkownik powinien móc dodawać własnych dostawców i własne klucze API.

---

# 13. Automatyczne wykrywanie i zarządzanie modelami

AI Operator powinien posiadać centralny:

**Model Registry**

oraz:

**Model Router**

Model Registry przechowuje informacje o dostępnych modelach, np.:

* dostawca,
* nazwa modelu,
* typ,
* możliwości,
* context window,
* koszt,
* dostępność,
* lokalność,
* limity,
* aktualny stan.

Model Router powinien wybierać model odpowiedni do zadania.

Przykład:

```text
proste pytanie
    → szybki tani model

trudne rozumowanie
    → mocny model reasoning

programowanie
    → model coding

prywatne zadanie
    → model lokalny

bardzo trudne zadanie
    → mocniejszy model API
```

AI Manager powinien móc wykorzystywać te informacje przy planowaniu i wykonywaniu zadań.

---

# 14. Zarządzanie limitami i kosztami

AI Operator powinien monitorować dostępność modeli i limity dostawców.

Powinien wiedzieć między innymi:

* czy model jest dostępny,
* czy przekroczono limit,
* kiedy limit może zostać odnowiony, jeżeli informacja jest dostępna,
* ile kosztowało użycie płatnego API,
* jaki jest dostępny budżet,
* czy można użyć alternatywnego modelu.

Przykład:

```text
Model A
limit: wykorzystany
reset: za 7 godzin

Model B
dostępny

Model C
dostępny lokalnie
```

AI Manager może wtedy automatycznie przełączyć się na Model B lub C, jeżeli polityka użytkownika na to pozwala.

---

# 15. Konta użytkownika u dostawców AI

Użytkownik może chcieć korzystać z własnych kont u różnych dostawców.

Architektura powinna umożliwiać bezpieczne zarządzanie autoryzacją, preferując:

1. oficjalne API,
2. oficjalne mechanizmy OAuth/autoryzacji,
3. bezpieczne przechowywanie tokenów,
4. jawne uprawnienia użytkownika.

AI Operator może monitorować dostępność i limity autoryzowanych usług.

Nie należy projektować mechanizmu obchodzenia zabezpieczeń, CAPTCHA, systemów antybotowych, wykrywania automatyzacji ani innych mechanizmów bezpieczeństwa usług.

AI Operator nie powinien udawać użytkownika w celu obejścia ograniczeń dostawcy.

Jeżeli dana usługa nie zezwala na określony rodzaj automatyzacji, AI Operator powinien wykorzystać oficjalnie dostępny interfejs/API albo nie korzystać z tej funkcji.

---

# 16. Automatyczny wybór dostawcy

AI Manager powinien docelowo podejmować decyzję nie tylko:

> „który model?”

ale:

> „który provider + który model + jaki sposób wykonania?”

Decyzja może uwzględniać:

* jakość,
* szybkość,
* koszt,
* prywatność,
* dostępność,
* limit,
* rodzaj zadania,
* długość kontekstu,
* wymagane możliwości,
* poziom ryzyka.

Przykład:

```text
zadanie: analiza prywatnych danych
→ preferuj model lokalny

zadanie: szybkie streszczenie
→ szybki tani model

zadanie: trudny kod
→ najlepszy dostępny model coding

provider niedostępny
→ fallback

limit wyczerpany
→ alternatywny provider/model
```

---

# 17. Computer Use

Docelowo AI Operator powinien posiadać możliwość kontrolowania komputera w sposób zbliżony do użytkownika.

Powinien móc, w ramach uprawnień:

* uruchamiać aplikacje,
* korzystać z myszy,
* korzystać z klawiatury,
* odczytywać ekran,
* wykonywać operacje w interfejsach aplikacji,
* przechodzić przez wieloetapowe procesy.

Przykład:

> „Otwórz Finorę i dodaj nowy wydatek.”

albo:

> „Otwórz aplikację X i sprawdź Y.”

Computer Use powinien być osobnym kontrolowanym systemem narzędziowym.

Nie należy dawać modelowi nieograniczonego dostępu do systemu operacyjnego.

Każda operacja powinna podlegać odpowiednim:

* uprawnieniom,
* ograniczeniom,
* politykom ryzyka,
* logowaniu,
* mechanizmom zatwierdzania.

---

# 18. Internet i Research

AI Operator powinien docelowo posiadać zdolność wykonywania aktualnego researchu.

Przykładowe polecenia:

> „Co ciekawego wydarzyło się dzisiaj?”

> „Znajdź mi najlepsze informacje o nowym modelu AI.”

> „Sprawdź, czy pojawiły się nowe informacje dotyczące mojego projektu.”

> „Znajdź promocję na iPada.”

System powinien umieć:

* wyszukiwać,
* porównywać źródła,
* filtrować wyniki,
* oceniać wiarygodność,
* streszczać informacje,
* zapamiętywać istotny kontekst,
* tworzyć zadania monitorujące.

---

# 19. System zdarzeń, scheduler i automatyzacje

AI Operator powinien posiadać centralny system:

**Events + Scheduler + Notifications + Automations**

Pozwoli to wykonywać zadania:

* o określonej godzinie,
* cyklicznie,
* po wystąpieniu zdarzenia,
* po spełnieniu warunku,
* po wykryciu określonej informacji.

Przykłady:

> „Codziennie o 8 rano powiedz mi najważniejsze informacje.”

> „Jeśli cena iPada spadnie poniżej X, poinformuj mnie.”

> „Jeżeli jutro będę miał wolną godzinę, przypomnij mi o treningu.”

> „Jeżeli znajdziesz nową informację o X, powiadom mnie.”

---

# 20. Notification Center

AI Operator powinien posiadać centralne centrum powiadomień.

Powiadomienia powinny posiadać między innymi:

* źródło,
* priorytet,
* kategorię,
* czas,
* stan,
* możliwość odłożenia,
* możliwość wykonania działania,
* możliwość wyciszenia podobnych powiadomień.

AI powinno ograniczać liczbę niepotrzebnych komunikatów.

---

# 21. Samodzielne inicjowanie działań

Docelowo AI Operator powinien mieć możliwość samodzielnego rozpoczęcia procesu, jeśli spełnione są odpowiednie warunki.

Przykład:

```text
AI obserwuje dozwolony kontekst
        ↓
wykrywa istotną informację
        ↓
ocenia wartość
        ↓
sprawdza politykę
        ↓
decyduje:
powiadom / zaproponuj / wykonaj
        ↓
użytkownik otrzymuje komunikat
```

Przykład:

> „Masz obecnie wolne 60 minut. W Twoim planie znajduje się trening, który można wykonać w tym czasie. Chcesz, żebym go otworzył?”

To nie powinno być nachalne.

AI powinno uczyć się, które sugestie są dla użytkownika wartościowe, a które ignorowane.

---

# 22. AI jako osobisty doradca

Docelowo AI Operator powinien łączyć informacje z wielu dozwolonych źródeł.

Przykład:

```text
Kalendarz
   +
zadania
   +
EVOLVE
   +
Finora
   +
historia pracy
   +
preferencje
   +
wydarzenia
   ↓
AI Manager
   ↓
kontekstowa sugestia
```

Dzięki temu może powiedzieć:

> „Masz godzinę wolnego, a kolejny zaplanowany blok EVOLVE trwa około 45 minut. Chcesz go teraz wykonać?”

albo:

> „Masz jutro dużo spotkań. Możemy dziś przygotować część zadań wcześniej.”

AI nie powinno podejmować ważnych decyzji za użytkownika bez odpowiednich uprawnień.

---

# 23. AI Operator jako system uczący się preferencji

AI powinien z czasem lepiej rozumieć użytkownika poprzez:

* świadomie zapisane informacje,
* historię rozmów,
* zatwierdzone dane,
* wyniki wcześniejszych interakcji,
* dozwolone źródła danych,
* reakcje użytkownika na sugestie.

Nie powinno to oznaczać niekontrolowanego gromadzenia wszystkiego.

Każde źródło danych powinno mieć jawny zakres dostępu i możliwość wyłączenia.

---

# 24. Samodoskonalenie

Docelowo AI Operator może analizować:

* własne błędy,
* niewykonane zadania,
* częste problemy,
* nieefektywne procesy,
* wykorzystanie modeli,
* wykorzystanie narzędzi,
* zachowanie użytkownika wobec sugestii.

Na tej podstawie może proponować:

* optymalizacje,
* nowe funkcje,
* zmiany architektury,
* nowe integracje,
* zmiany modeli,
* usprawnienia UX.

Samodzielna implementacja powinna nadal podlegać:

```text
propozycja
   ↓
ocena
   ↓
zgoda / polityka autonomii
   ↓
plan
   ↓
implementacja
   ↓
testy
   ↓
weryfikacja
   ↓
checkpoint
```

---

# 25. AI Operator jako autonomiczny programista

Jednym z najważniejszych celów projektu jest możliwość samodzielnego rozwijania oprogramowania.

Docelowy przepływ:

```text
użytkownik / AI propozycja
          ↓
AI Manager
          ↓
plan
          ↓
task graph
          ↓
Development Agent
          ↓
Coding Model
          ↓
Workspace
          ↓
Tools
          ↓
Tests / Build / Verification
          ↓
problem?
     ┌────┴────┐
    TAK        NIE
     ↓          ↓
  Repair       Commit
     ↓
  Verification
     ↓
  Commit
```

AI Operator powinien docelowo być zdolny do rozwijania zarówno innych projektów użytkownika, jak i własnego kodu.

Autonomia w tym zakresie powinna jednak rosnąć stopniowo.

---

# 26. Główna filozofia bezpieczeństwa

Im większe możliwości AI Operatora, tym ważniejsze stają się:

* Permission Engine,
* Approval Service,
* Tool Execution Service,
* Audit,
* fail-closed,
* ograniczenie zakresu narzędzi,
* izolacja workspace,
* Verification,
* kontrola kosztów,
* kontrola danych,
* kontrola dostępu do urządzeń,
* jawne polityki autonomii.

Najważniejsza zasada:

> **AI Operator może być bardzo autonomiczny, ale jego autonomia musi być ograniczona przez jawne polityki i mechanizmy bezpieczeństwa, a nie przez zaufanie do samego modelu AI.**

---

# 27. Docelowy poziom systemu

Docelowo AI Operator powinien umożliwiać użytkownikowi powiedzenie praktycznie naturalnym językiem:

> „Zrób X.”

a system sam:

1. zrozumie intencję,
2. sprawdzi, czy ma wystarczające informacje,
3. dopyta, jeżeli czegoś brakuje,
4. określi potrzebne narzędzia,
5. wybierze odpowiedni model,
6. sprawdzi uprawnienia,
7. zaplanuje wykonanie,
8. wykona zadanie,
9. zweryfikuje rezultat,
10. naprawi problemy, jeżeli jest to możliwe,
11. poprosi o zgodę, jeżeli jest wymagana,
12. poinformuje użytkownika o rezultacie,
13. zapamięta istotny kontekst, jeżeli polityka na to pozwala.

To powinno być centralne doświadczenie użytkownika.

---

# 28. Priorytet rozwoju

Rozwój powinien następować etapowo.

Najpierw:

```text
PLAN
 ↓
TASK
 ↓
CODE
 ↓
TOOLS
 ↓
VERIFY
 ↓
REPAIR
 ↓
COMMIT
```

Następnie:

```text
VOICE
MEMORY
WEB
EVENTS
SCHEDULER
NOTIFICATIONS
MODEL ROUTING
PROVIDER MANAGEMENT
```

Następnie:

```text
COMPUTER USE
APPLICATION INTEGRATIONS
PROACTIVE ASSISTANCE
WATCHLISTS
PERSONAL CONTEXT
```

Następnie:

```text
SELF-IMPROVEMENT
AUTONOMOUS DEVELOPMENT
ADVANCED AGENT TEAMS
MULTI-DEVICE CONTROL
```

Kolejne funkcje nie powinny być implementowane tylko dlatego, że są możliwe.

Każda funkcja powinna odpowiadać na pytanie:

> **Czy rzeczywiście zwiększa użyteczność AI Operatora?**

---

# 29. Docelowa definicja AI Operatora

AI Operator nie powinien być traktowany jako jeden model AI.

Powinien być traktowany jako:

> **system operacyjny dla osobistej inteligencji użytkownika**

Składający się z:

* AI Managera,
* Model Routera,
* Model Registry,
* Memory,
* Event System,
* Scheduler,
* Notification Center,
* Tools,
* Agents,
* Permission Engine,
* Approval Service,
* Audit,
* Computer Use,
* Provider Management,
* Task System,
* Verification,
* autonomicznego development loop,
* interfejsu głosowego,
* interfejsu tekstowego,
* wyspecjalizowanych integracji.

Modele AI są tylko jednym z elementów tego systemu.

---

# 30. Aktualny kierunek architektoniczny

Obecna architektura powinna pozostać provider-neutral i capability-oriented.

W szczególności:

* AI Manager nie powinien być przywiązany do jednego modelu,
* DevelopmentAgent nie powinien być przywiązany do jednego providera,
* narzędzia powinny być niezależne od konkretnego modelu,
* uprawnienia powinny być egzekwowane poza modelem,
* pamięć powinna być niezależna od providera,
* scheduler i event system powinny być niezależne od providera,
* głos i tekst powinny prowadzić do wspólnego systemu wykonawczego,
* nowe providery powinny być możliwe do dodania bez przebudowy rdzenia.

Docelowo możliwe powinno być dodanie kolejnego providera/modelu bez zmiany logiki biznesowej AI Operatora.

---

# 31. Najważniejsza zasada projektowa

AI Operator powinien stopniowo przechodzić:

**reaktywny → kontekstowy → proaktywny → autonomiczny**

ale nigdy:

**reaktywny → niekontrolowany.**

Autonomia ma rosnąć razem z:

* bezpieczeństwem,
* obserwowalnością,
* weryfikacją,
* kontrolą uprawnień,
* jakością modeli,
* jakością pamięci,
* odpornością systemu.

---

# 32. Docelowe doświadczenie użytkownika

Najważniejsze jest, aby użytkownik nie musiał myśleć:

> „Którego agenta mam uruchomić?”

> „Który model mam wybrać?”

> „Które narzędzie mam wybrać?”

> „Jak mam wykonać tę operację?”

Użytkownik powinien przede wszystkim powiedzieć:

> **„Zrób X.”**

AI Operator powinien samodzielnie ustalić:

* co użytkownik chce osiągnąć,
* czego potrzebuje,
* jakich informacji mu brakuje,
* jakich narzędzi potrzebuje,
* który model jest odpowiedni,
* czy posiada wymagane uprawnienia,
* czy powinien zapytać użytkownika,
* jak zweryfikować rezultat.

To jest główny produkt końcowy projektu.

---

# 33. Ostateczna wizja

Docelowo użytkownik powinien móc rozpocząć dzień od powiedzenia:

> „Co dzisiaj powinienem wiedzieć?”

AI Operator może wtedy przygotować spersonalizowaną odpowiedź na podstawie aktualnych informacji, kalendarza, zadań, zainteresowań i dozwolonego kontekstu.

W ciągu dnia użytkownik może powiedzieć:

> „Otwórz Finorę i dodaj ten wydatek.”

AI może wykonać operację.

Może również sam powiedzieć:

> „Znalazłem promocję, którą kazałeś mi monitorować.”

Może zasugerować:

> „Masz teraz godzinę wolnego. Chcesz wykonać zaplanowany trening?”

Może ostrzec:

> „Od czterech godzin pracujesz przy komputerze. Sugeruję przerwę.”

Może zaproponować:

> „Znalazłem sposób na usprawnienie własnego działania. Chcesz, żebym przygotował plan zmiany?”

A gdy użytkownik powie:

> „Tak, zrób to.”

AI Operator powinien w przyszłości sam:

**zaplanować → zaprogramować → przetestować → naprawić → zweryfikować → przedstawić rezultat.**

To jest docelowy kierunek całego projektu.

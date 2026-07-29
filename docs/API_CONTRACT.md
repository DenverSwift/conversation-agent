# API Contract - Telegram Human Communication Agent

> **Статус:** Draft v1  
> **Назначение:** контракт между frontend внутренней панели и backend системы  
> **Область:** первый рабочий вертикальный сценарий и базовые операционные функции  
> **Base URL:** `/api/v1`  
> **Транспорт:** REST + WebSocket или SSE для realtime-событий

---

## 1. Назначение документа

Этот документ фиксирует минимальный API-контракт, на котором frontend и backend могут разрабатываться параллельно.

Контракт покрывает основной рабочий сценарий:

1. Пользователь входит во внутреннюю панель.
2. Просматривает подключенные Telegram-аккаунты.
3. Просматривает и настраивает агентов.
4. Назначает агента Telegram-аккаунту.
5. Открывает список диалогов.
6. Просматривает сообщения и AI draft.
7. Подтверждает, исправляет, отклоняет draft или выбирает `no response`.
8. Перехватывает диалог вручную.
9. Возвращает управление агенту.
10. Просматривает аудит действий и состояние системы.

Документ не является полной спецификацией финального продукта. Новые разделы API должны добавляться без нарушения закрепленных здесь правил совместимости.

---

## 2. Основные принципы

### 2.1. Backend является источником истины

Frontend не должен самостоятельно определять:

- разрешено ли действие пользователю;
- является ли draft актуальным;
- можно ли отправлять сообщение;
- активен ли emergency stop;
- какой агент назначен диалогу;
- завершилось ли действие успешно в Telegram.

Backend возвращает актуальное состояние объекта и доступные действия.

### 2.2. Provenance не изменяется задним числом

Каждое сообщение и каждое исправление имеют явно зафиксированное происхождение.

AI draft нельзя переименовать в человеческий ответ. При исправлении сохраняются оба объекта:

- исходный AI draft;
- human correction.

### 2.3. Stale draft нельзя отправить

Draft становится `stale`, если контекст, на основании которого он был создан, изменился.

Типичные причины:

- пришло новое входящее сообщение;
- оператор отправил ручное сообщение;
- произошел human takeover;
- изменился активный агент;
- изменились критические правила диалога;
- диалог был поставлен на паузу или заблокирован.

Любая попытка подтвердить stale draft должна возвращать `409 Conflict`.

### 2.4. Опасные действия идемпотентны

Запросы, которые могут привести к отправке сообщения или изменению критического состояния, должны поддерживать заголовок:

```http
Idempotency-Key: <unique-client-generated-key>
```

Повторный запрос с тем же ключом не должен создавать повторную отправку или повторное действие.

### 2.5. Optimistic concurrency

Редактируемые и операционные сущности имеют поле `version`.

Команды изменения передают ожидаемую версию:

```json
{
  "expectedVersion": 12
}
```

Если объект уже изменился, backend возвращает `409 VERSION_CONFLICT` и актуальную версию.

### 2.6. Realtime не заменяет REST

Realtime-события сообщают об изменениях, но REST остается способом получить полное актуальное состояние.

После reconnect, разрыва последовательности событий или неизвестного состояния frontend обязан повторно загрузить соответствующий объект через REST.

---

## 3. Общие соглашения

### 3.1. Формат запросов

```text
Content-Type: application/json
Accept: application/json
Authorization: Bearer <access_token>
```

### 3.2. Идентификаторы

Рекомендуемый формат - UUIDv7, ULID или другой сортируемый уникальный ID.

Публичный формат ID должен быть строкой и не должен раскрывать внутренние последовательные database ID.

Примеры:

```text
usr_01K1...
tga_01K1...
agt_01K1...
cnv_01K1...
msg_01K1...
drf_01K1...
```

Префиксы необязательны, но должны использоваться последовательно во всей системе.

### 3.3. Время

Все timestamps передаются в ISO 8601 и UTC:

```json
{
  "createdAt": "2026-07-29T09:48:13.284Z"
}
```

Frontend отвечает за отображение времени в выбранном часовом поясе пользователя.

Рабочие часы хранят собственный IANA timezone:

```json
{
  "timezone": "Europe/Kyiv"
}
```

### 3.4. Имена полей

JSON использует `camelCase`.

### 3.5. Null и отсутствующие поля

- Поле отсутствует, если оно не входит в выбранную representation.
- `null` используется, если поле входит в схему, но значение отсутствует.
- Пустой массив используется, если коллекция известна и пуста.

### 3.6. Пагинация

Используется cursor pagination.

Запрос:

```http
GET /api/v1/conversations?limit=30&cursor=<cursor>
```

Ответ:

```json
{
  "data": [],
  "meta": {
    "nextCursor": "eyJpZCI6IjEyMyJ9",
    "hasMore": true,
    "requestId": "req_01K1..."
  }
}
```

### 3.7. Сортировка

Формат:

```http
GET /api/v1/conversations?sort=-updatedAt
```

- `updatedAt` - по возрастанию;
- `-updatedAt` - по убыванию.

### 3.8. Фильтры

Фильтры передаются query-параметрами.

Пример:

```http
GET /api/v1/conversations?accountId=tga_123&status=approval_required&lowConfidence=true
```

### 3.9. Общий успешный ответ

```json
{
  "data": {},
  "meta": {
    "requestId": "req_01K1...",
    "timestamp": "2026-07-29T09:48:13.284Z"
  }
}
```

### 3.10. Общая ошибка

```json
{
  "error": {
    "code": "DRAFT_STALE",
    "message": "Draft is no longer valid",
    "details": {
      "draftId": "drf_123",
      "staleReason": "new_incoming_message"
    },
    "retryable": false
  },
  "meta": {
    "requestId": "req_01K1...",
    "timestamp": "2026-07-29T09:48:13.284Z"
  }
}
```

### 3.11. HTTP-коды

| Код | Значение |
|---|---|
| `200` | Запрос успешно выполнен |
| `201` | Объект создан |
| `202` | Команда принята в асинхронную обработку |
| `204` | Действие выполнено, тело ответа отсутствует |
| `400` | Некорректный запрос |
| `401` | Пользователь не авторизован |
| `403` | Недостаточно прав |
| `404` | Объект не найден |
| `409` | Конфликт состояния или версии |
| `422` | Ошибка валидации данных |
| `429` | Превышен лимит запросов |
| `500` | Внутренняя ошибка |
| `503` | Сервис временно недоступен |

---

## 4. Общие enum

```ts
export type InternalUserRole =
  | "administrator"
  | "operator"
  | "trainer"
  | "observer";

export type InternalUserStatus =
  | "active"
  | "disabled";

export type AccountStatus =
  | "connecting"
  | "connected"
  | "disconnected"
  | "authorization_required"
  | "paused"
  | "error";

export type AgentStatus =
  | "draft"
  | "active"
  | "paused"
  | "archived";

export type ConversationStatus =
  | "paused"
  | "approval_required"
  | "agent_active"
  | "human_takeover"
  | "closed"
  | "blocked"
  | "error";

export type MessageProvenance =
  | "contact"
  | "human_operator"
  | "imported_human_history"
  | "ai_draft"
  | "ai_sent"
  | "human_correction"
  | "system_event";

export type MessageDeliveryStatus =
  | "draft"
  | "queued"
  | "sending"
  | "sent"
  | "failed"
  | "edited"
  | "deleted";

export type DraftStatus =
  | "generating"
  | "pending_approval"
  | "approved"
  | "edited"
  | "rejected"
  | "stale"
  | "cancelled"
  | "sent"
  | "failed";

export type ApprovalMode =
  | "automatic"
  | "manual"
  | "low_confidence_only";

export type PendingAction =
  | "none"
  | "generating"
  | "waiting_for_approval"
  | "scheduled_send"
  | "handoff_suggested"
  | "retry_required";

export type InteractionMode =
  | "casual"
  | "formal"
  | "friendly"
  | "friendly_business"
  | "business"
  | "support"
  | "conflict"
  | "emotional_support"
  | "urgent"
  | "ambiguous";

export type StaleReason =
  | "new_incoming_message"
  | "human_message_sent"
  | "human_takeover"
  | "agent_changed"
  | "conversation_paused"
  | "conversation_blocked"
  | "critical_configuration_changed"
  | "manual_invalidation";
```

Enum расширяются только обратно совместимым способом. Frontend должен иметь fallback для неизвестного значения.

---

## 5. Базовые схемы

### 5.1. InternalUser

```ts
export interface InternalUser {
  id: string;
  name: string;
  email: string;
  role: InternalUserRole;
  status: InternalUserStatus;
  permissions: string[];
  lastActiveAt: string | null;
  createdAt: string;
  updatedAt: string;
  version: number;
}
```

### 5.2. TelegramAccountSummary

```ts
export interface TelegramAccountSummary {
  id: string;
  telegramUserId: string;
  displayName: string;
  username: string | null;
  phoneMasked: string | null;
  status: AccountStatus;
  isPaused: boolean;
  lastActivityAt: string | null;
  activeConversationCount: number;
  assignedAgent: AgentReference | null;
  responsibleUser: UserReference | null;
  health: {
    connection: "healthy" | "degraded" | "unhealthy";
    lastError: ApiErrorSummary | null;
  };
  version: number;
  createdAt: string;
  updatedAt: string;
}
```

### 5.3. AgentReference

```ts
export interface AgentReference {
  id: string;
  name: string;
  version: number;
}
```

### 5.4. UserReference

```ts
export interface UserReference {
  id: string;
  name: string;
}
```

### 5.5. ContactReference

```ts
export interface ContactReference {
  id: string;
  telegramUserId: string;
  displayName: string;
  username: string | null;
}
```

### 5.6. MessageContent

```ts
export type MessageContent =
  | {
      type: "text";
      text: string;
    }
  | {
      type: "image";
      fileId: string;
      caption: string | null;
      mimeType: string | null;
    }
  | {
      type: "document";
      fileId: string;
      fileName: string | null;
      caption: string | null;
      mimeType: string | null;
    }
  | {
      type: "voice";
      fileId: string;
      durationSeconds: number | null;
      transcript: string | null;
    }
  | {
      type: "system_event";
      eventType: string;
      text: string;
    };
```

### 5.7. Message

```ts
export interface Message {
  id: string;
  conversationId: string;
  telegramMessageId: string | null;
  provenance: MessageProvenance;
  author: {
    id: string | null;
    displayName: string;
  };
  content: MessageContent;
  deliveryStatus: MessageDeliveryStatus;
  replyToMessageId: string | null;
  editedAt: string | null;
  deletedAt: string | null;
  createdAt: string;

  agentId: string | null;
  agentVersion: number | null;
  model: string | null;
  configurationFingerprint: string | null;

  correctionOfMessageId: string | null;
  sourceDraftId: string | null;
}
```

### 5.8. DraftPart

```ts
export interface DraftPart {
  id: string;
  text: string;
  order: number;
  suggestedDelayMs: number;
}
```

### 5.9. Draft

```ts
export interface Draft {
  id: string;
  conversationId: string;
  status: DraftStatus;
  stale: boolean;
  staleReason: StaleReason | null;
  textParts: DraftPart[];
  confidence: number | null;
  generatedAt: string;
  basedOnLastMessageId: string;
  configurationFingerprint: string;
  explanation: DraftExplanation;
  version: number;
}
```

### 5.10. DraftExplanation

Операционная объяснимость не должна содержать скрытый chain-of-thought.

```ts
export interface DraftExplanation {
  goal: string;
  intent: string;
  interactionMode: InteractionMode;
  memoryIds: string[];
  exampleIds: string[];
  knowledgeSourceIds: string[];
  styleRuleIds: string[];
  relationshipRuleIds: string[];
  behaviorPlan: {
    action: "reply" | "no_response" | "wait" | "handoff";
    bubbleCount: number;
    useTyping: boolean;
    replyToMessageId: string | null;
  };
}
```

### 5.11. ConversationPermissions

```ts
export interface ConversationPermissions {
  canReply: boolean;
  canApprove: boolean;
  canEditDraft: boolean;
  canRejectDraft: boolean;
  canChooseNoResponse: boolean;
  canTakeOver: boolean;
  canReturnToAgent: boolean;
  canPause: boolean;
  canResume: boolean;
  canClose: boolean;
  canBlock: boolean;
  canEditMemory: boolean;
}
```

### 5.12. ConversationSummary

```ts
export interface ConversationSummary {
  id: string;
  account: {
    id: string;
    displayName: string;
  };
  contact: ContactReference;
  agent: AgentReference | null;
  status: ConversationStatus;
  pendingAction: PendingAction;
  intent: string | null;
  interactionMode: InteractionMode | null;
  confidence: number | null;
  lastMessage: Message | null;
  assignedOperator: UserReference | null;
  warnings: ConversationWarning[];
  unreadCount: number;
  version: number;
  updatedAt: string;
}
```

### 5.13. ConversationWarning

```ts
export interface ConversationWarning {
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
}
```

### 5.14. ApiErrorSummary

```ts
export interface ApiErrorSummary {
  code: string;
  message: string;
  occurredAt: string;
}
```

---

## 6. Авторизация

### 6.1. Вход

```http
POST /api/v1/auth/login
```

Запрос:

```json
{
  "email": "operator@example.com",
  "password": "secret-password"
}
```

Ответ:

```json
{
  "data": {
    "accessToken": "eyJhbGciOi...",
    "expiresAt": "2026-07-29T11:00:00.000Z",
    "user": {
      "id": "usr_123",
      "name": "Алексей",
      "email": "operator@example.com",
      "role": "operator",
      "status": "active"
    }
  }
}
```

### 6.2. Получить текущего пользователя

```http
GET /api/v1/auth/me
```

Ответ:

```json
{
  "data": {
    "id": "usr_123",
    "name": "Алексей",
    "email": "operator@example.com",
    "role": "operator",
    "status": "active",
    "permissions": [
      "conversations.read",
      "conversations.reply",
      "conversations.takeover",
      "drafts.approve",
      "drafts.edit",
      "drafts.reject"
    ]
  }
}
```

### 6.3. Выход

```http
POST /api/v1/auth/logout
```

### 6.4. Обновление токена

```http
POST /api/v1/auth/refresh
```

Точная схема refresh token зависит от выбранной модели авторизации и должна быть закреплена отдельно.

---

## 7. Telegram Accounts API

### 7.1. Список аккаунтов

```http
GET /api/v1/telegram-accounts
```

Фильтры:

| Параметр | Тип | Описание |
|---|---|---|
| `status` | `AccountStatus` | Фильтр по состоянию |
| `agentId` | `string` | Аккаунты с назначенным агентом |
| `responsibleUserId` | `string` | Ответственный сотрудник |
| `search` | `string` | Поиск по имени и username |
| `limit` | `number` | Размер страницы |
| `cursor` | `string` | Курсор |
| `sort` | `string` | Сортировка |

Пример ответа:

```json
{
  "data": [
    {
      "id": "tga_123",
      "telegramUserId": "583920183",
      "displayName": "Матвей",
      "username": "matvey_project",
      "phoneMasked": "+380******52",
      "status": "connected",
      "isPaused": false,
      "lastActivityAt": "2026-07-29T09:45:22.000Z",
      "activeConversationCount": 7,
      "assignedAgent": {
        "id": "agt_123",
        "name": "Sales Matvey",
        "version": 4
      },
      "responsibleUser": {
        "id": "usr_123",
        "name": "Алексей"
      },
      "health": {
        "connection": "healthy",
        "lastError": null
      },
      "version": 12,
      "createdAt": "2026-07-20T12:00:00.000Z",
      "updatedAt": "2026-07-29T09:45:22.000Z"
    }
  ],
  "meta": {
    "nextCursor": null,
    "hasMore": false
  }
}
```

### 7.2. Карточка аккаунта

```http
GET /api/v1/telegram-accounts/{accountId}
```

Карточка дополнительно возвращает:

- состояние авторизации;
- состояние Telegram session;
- рабочие часы;
- правила доступа;
- разрешенные контакты и чаты;
- активные диалоги;
- последние ошибки;
- retention policy;
- доступные действия.

### 7.3. Начать подключение аккаунта

```http
POST /api/v1/telegram-accounts
```

Пример запроса:

```json
{
  "phone": "+380000000000",
  "responsibleUserId": "usr_123"
}
```

Ответ может содержать состояние следующего шага авторизации:

```json
{
  "data": {
    "accountId": "tga_123",
    "authorizationState": "code_required",
    "authorizationSessionId": "auth_session_123"
  }
}
```

Telegram authorization flow должен быть описан отдельным разделом после выбора backend-библиотеки и способа хранения сессий.

### 7.4. Подтвердить код авторизации

```http
POST /api/v1/telegram-accounts/{accountId}/authorization/code
```

```json
{
  "authorizationSessionId": "auth_session_123",
  "code": "12345"
}
```

### 7.5. Подтвердить пароль 2FA

```http
POST /api/v1/telegram-accounts/{accountId}/authorization/password
```

```json
{
  "authorizationSessionId": "auth_session_123",
  "password": "secret"
}
```

### 7.6. Поставить аккаунт на паузу

```http
POST /api/v1/telegram-accounts/{accountId}/pause
```

```json
{
  "reason": "manual_operator_pause",
  "expectedVersion": 12
}
```

### 7.7. Возобновить аккаунт

```http
POST /api/v1/telegram-accounts/{accountId}/resume
```

```json
{
  "expectedVersion": 13
}
```

### 7.8. Переподключить аккаунт

```http
POST /api/v1/telegram-accounts/{accountId}/reconnect
```

### 7.9. Отключить аккаунт

```http
POST /api/v1/telegram-accounts/{accountId}/disconnect
```

### 7.10. Безопасно удалить аккаунт

```http
DELETE /api/v1/telegram-accounts/{accountId}
```

Удаление должно требовать явного подтверждения и не должно молча удалять аудит.

---

## 8. Agents API

### 8.1. Список агентов

```http
GET /api/v1/agents
```

Фильтры:

- `status`;
- `accountId`;
- `search`;
- `limit`;
- `cursor`;
- `sort`.

Пример ответа:

```json
{
  "data": [
    {
      "id": "agt_123",
      "name": "Sales Matvey",
      "description": "Общается с входящими клиентами",
      "role": "sales_communication",
      "status": "active",
      "languages": ["ru", "uk", "en"],
      "personalityVersion": 4,
      "styleVersion": 8,
      "knowledgeVersion": 3,
      "behaviorVersion": 5,
      "activeConversationCount": 7,
      "assignedAccountCount": 1,
      "quality": {
        "approvalRate": 0.81,
        "editRate": 0.12,
        "rejectionRate": 0.07
      },
      "version": 19,
      "updatedAt": "2026-07-29T08:30:00.000Z"
    }
  ]
}
```

### 8.2. Создать агента

```http
POST /api/v1/agents
```

```json
{
  "name": "Sales Matvey",
  "description": "Общается с входящими клиентами",
  "role": "sales_communication",
  "languages": ["ru", "uk"]
}
```

Новый агент создается в статусе `draft`, если явно не закреплено другое правило.

### 8.3. Получить агента

```http
GET /api/v1/agents/{agentId}
```

Пример сокращенного ответа:

```json
{
  "data": {
    "id": "agt_123",
    "name": "Sales Matvey",
    "description": "Общается с входящими клиентами",
    "role": "sales_communication",
    "status": "active",
    "languages": ["ru", "uk", "en"],
    "configuration": {
      "identity": {
        "displayName": "Матвей",
        "organization": "Our Project",
        "conversationRole": "Представитель проекта",
        "allowedSelfDescriptions": [
          "Я занимаюсь проектом"
        ],
        "forbiddenSelfDescriptions": [
          "Я сотрудник Telegram"
        ]
      },
      "goals": {
        "primaryGoal": "Понять запрос и продолжить полезный диалог",
        "allowedGoals": [
          "clarify_request",
          "explain_product",
          "request_details"
        ],
        "forbiddenGoals": [
          "make_legal_commitment",
          "make_financial_commitment"
        ]
      },
      "personality": {
        "warmth": 72,
        "formality": 28,
        "directness": 68,
        "confidence": 75,
        "patience": 70,
        "humor": 42,
        "emotionality": 45,
        "assertiveness": 55,
        "initiative": 61,
        "curiosity": 64,
        "answerDepth": 48
      },
      "voice": {
        "averageMessageLength": "short",
        "sentenceLength": "short",
        "lowercasePreferred": true,
        "emojiFrequency": "rare",
        "slangLevel": "medium",
        "profanityLevel": "contextual",
        "multiBubbleEnabled": true,
        "maxBubbles": 3,
        "forbiddenPhrases": [
          "Чем я могу вам помочь?",
          "Я понимаю ваши чувства"
        ]
      },
      "telegramBehavior": {
        "incomingAggregation": {
          "initialWaitMs": 2500,
          "extendOnMessageMs": 2000,
          "maximumWaitMs": 10000
        },
        "readDelay": {
          "minimumMs": 1200,
          "maximumMs": 7500
        },
        "typing": {
          "enabled": true,
          "charactersPerSecondMin": 5,
          "charactersPerSecondMax": 11,
          "maximumDurationMs": 18000
        },
        "bubblePause": {
          "minimumMs": 600,
          "maximumMs": 2300
        },
        "interruptionPolicy": "cancel_unsent_and_replan",
        "noResponsePolicy": "allow"
      },
      "handoff": {
        "lowConfidenceThreshold": 0.45,
        "sensitiveTopics": [
          "legal",
          "financial_commitment",
          "threats"
        ],
        "assignedOperatorId": "usr_123"
      }
    },
    "versions": {
      "personality": 4,
      "style": 8,
      "knowledge": 3,
      "behavior": 5
    },
    "version": 19
  }
}
```

### 8.4. Обновить основные поля агента

```http
PATCH /api/v1/agents/{agentId}
```

### 8.5. Обновить отдельную секцию агента

Рекомендуется сохранять крупные секции отдельно.

```http
PATCH /api/v1/agents/{agentId}/identity
PATCH /api/v1/agents/{agentId}/goals
PATCH /api/v1/agents/{agentId}/personality
PATCH /api/v1/agents/{agentId}/voice
PATCH /api/v1/agents/{agentId}/telegram-behavior
PATCH /api/v1/agents/{agentId}/emotion-empathy
PATCH /api/v1/agents/{agentId}/contact-adaptation
PATCH /api/v1/agents/{agentId}/memory-policy
PATCH /api/v1/agents/{agentId}/start-conditions
PATCH /api/v1/agents/{agentId}/handoff-safety
```

Пример:

```json
{
  "warmth": 80,
  "formality": 20,
  "humor": 50,
  "changeComment": "Сделал агента менее формальным",
  "expectedVersion": 19
}
```

### 8.6. Активировать агента

```http
POST /api/v1/agents/{agentId}/activate
```

### 8.7. Поставить агента на паузу

```http
POST /api/v1/agents/{agentId}/pause
```

### 8.8. Архивировать агента

```http
POST /api/v1/agents/{agentId}/archive
```

### 8.9. Дублировать агента

```http
POST /api/v1/agents/{agentId}/duplicate
```

### 8.10. История версий

```http
GET /api/v1/agents/{agentId}/versions
GET /api/v1/agents/{agentId}/versions/{versionId}
POST /api/v1/agents/{agentId}/versions/{versionId}/rollback
```

---

## 9. Assignments API

### 9.1. Список назначений

```http
GET /api/v1/assignments
```

### 9.2. Создать назначение

```http
POST /api/v1/assignments
```

```json
{
  "accountId": "tga_123",
  "agentId": "agt_123",
  "priority": 100,
  "approvalMode": "manual",
  "allowedChatIds": [],
  "activeFrom": "2026-07-29T00:00:00.000Z",
  "activeUntil": null
}
```

Ответ:

```json
{
  "data": {
    "id": "asn_123",
    "accountId": "tga_123",
    "agentId": "agt_123",
    "priority": 100,
    "approvalMode": "manual",
    "status": "active",
    "createdAt": "2026-07-29T10:00:00.000Z",
    "version": 1
  }
}
```

### 9.3. Конфликт назначения

Backend не может молча выбрать агента при конфликте правил.

```json
{
  "error": {
    "code": "ASSIGNMENT_CONFLICT",
    "message": "Another agent has an assignment with the same priority",
    "details": {
      "conflictingAssignmentId": "asn_456",
      "priority": 100
    },
    "retryable": false
  }
}
```

### 9.4. Изменить назначение

```http
PATCH /api/v1/assignments/{assignmentId}
```

### 9.5. Удалить назначение

```http
DELETE /api/v1/assignments/{assignmentId}
```

---

## 10. Conversations Inbox API

### 10.1. Список диалогов

```http
GET /api/v1/conversations
```

Поддерживаемые фильтры:

| Параметр | Описание |
|---|---|
| `accountId` | Telegram-аккаунт |
| `agentId` | Активный агент |
| `contactId` | Контакт |
| `status` | Состояние диалога |
| `pendingAction` | Ожидаемое действие |
| `assignedOperatorId` | Назначенный оператор |
| `lowConfidence` | Диалоги с низкой уверенностью |
| `unresolved` | Незавершенные диалоги |
| `hasWarnings` | Есть предупреждения |
| `search` | Поиск по контакту и тексту |
| `limit` | Размер страницы |
| `cursor` | Курсор |
| `sort` | Сортировка |

Пример:

```http
GET /api/v1/conversations?accountId=tga_123&status=approval_required&pendingAction=waiting_for_approval&limit=30
```

Ответ:

```json
{
  "data": [
    {
      "id": "cnv_123",
      "account": {
        "id": "tga_123",
        "displayName": "Матвей"
      },
      "contact": {
        "id": "cnt_123",
        "telegramUserId": "882913001",
        "displayName": "Денис",
        "username": "denis_dev"
      },
      "agent": {
        "id": "agt_123",
        "name": "Sales Matvey",
        "version": 4
      },
      "status": "approval_required",
      "pendingAction": "waiting_for_approval",
      "intent": "service_inquiry",
      "interactionMode": "friendly_business",
      "confidence": 0.82,
      "lastMessage": {
        "id": "msg_123",
        "conversationId": "cnv_123",
        "telegramMessageId": "1453",
        "provenance": "contact",
        "author": {
          "id": "cnt_123",
          "displayName": "Денис"
        },
        "content": {
          "type": "text",
          "text": "А сколько примерно будет стоить?"
        },
        "deliveryStatus": "sent",
        "replyToMessageId": null,
        "editedAt": null,
        "deletedAt": null,
        "createdAt": "2026-07-29T09:44:00.000Z",
        "agentId": null,
        "agentVersion": null,
        "model": null,
        "configurationFingerprint": null,
        "correctionOfMessageId": null,
        "sourceDraftId": null
      },
      "assignedOperator": {
        "id": "usr_123",
        "name": "Алексей"
      },
      "warnings": [],
      "unreadCount": 1,
      "version": 37,
      "updatedAt": "2026-07-29T09:44:04.000Z"
    }
  ]
}
```

---

## 11. Conversation Detail API

### 11.1. Получить карточку диалога

```http
GET /api/v1/conversations/{conversationId}
```

Пример ответа:

```json
{
  "data": {
    "id": "cnv_123",
    "status": "approval_required",
    "pendingAction": "waiting_for_approval",
    "version": 37,
    "account": {
      "id": "tga_123",
      "displayName": "Матвей",
      "status": "connected"
    },
    "contact": {
      "id": "cnt_123",
      "displayName": "Денис",
      "username": "denis_dev",
      "language": "ru",
      "automationAllowed": true
    },
    "agent": {
      "id": "agt_123",
      "name": "Sales Matvey",
      "personalityVersion": 4,
      "styleVersion": 8,
      "knowledgeVersion": 3,
      "behaviorVersion": 5
    },
    "state": {
      "intent": "service_inquiry",
      "interactionMode": "friendly_business",
      "emotionalState": {
        "label": "neutral",
        "confidence": 0.74
      },
      "currentGoal": "clarify_scope_before_price",
      "openQuestions": [
        "Какой объем автоматизации требуется?"
      ],
      "nextExpectedActor": "agent"
    },
    "relationshipProfile": {
      "relationshipType": "new_contact",
      "formality": 45,
      "warmth": 50,
      "trust": 20,
      "humorAllowed": false,
      "profanityAllowed": false,
      "confidence": 0.31
    },
    "messages": [
      {
        "id": "msg_101",
        "conversationId": "cnv_123",
        "telegramMessageId": "1452",
        "provenance": "contact",
        "author": {
          "id": "cnt_123",
          "displayName": "Денис"
        },
        "content": {
          "type": "text",
          "text": "Привет"
        },
        "deliveryStatus": "sent",
        "replyToMessageId": null,
        "editedAt": null,
        "deletedAt": null,
        "createdAt": "2026-07-29T09:43:51.000Z",
        "agentId": null,
        "agentVersion": null,
        "model": null,
        "configurationFingerprint": null,
        "correctionOfMessageId": null,
        "sourceDraftId": null
      }
    ],
    "activeDraft": {
      "id": "drf_123",
      "conversationId": "cnv_123",
      "status": "pending_approval",
      "stale": false,
      "staleReason": null,
      "textParts": [
        {
          "id": "part_1",
          "text": "Привет",
          "order": 1,
          "suggestedDelayMs": 0
        },
        {
          "id": "part_2",
          "text": "Зависит от того, что именно нужно автоматизировать",
          "order": 2,
          "suggestedDelayMs": 1200
        },
        {
          "id": "part_3",
          "text": "Можешь коротко описать задачу?",
          "order": 3,
          "suggestedDelayMs": 900
        }
      ],
      "confidence": 0.82,
      "generatedAt": "2026-07-29T09:44:04.000Z",
      "basedOnLastMessageId": "msg_123",
      "configurationFingerprint": "cfg_sha256_b902fa",
      "explanation": {
        "goal": "clarify_scope_before_price",
        "intent": "service_inquiry",
        "interactionMode": "friendly_business",
        "memoryIds": [],
        "exampleIds": ["exm_123"],
        "knowledgeSourceIds": ["knw_price_policy_v3"],
        "styleRuleIds": [
          "sty_short_messages",
          "sty_no_formal_greeting"
        ],
        "relationshipRuleIds": [],
        "behaviorPlan": {
          "action": "reply",
          "bubbleCount": 3,
          "useTyping": true,
          "replyToMessageId": "msg_123"
        }
      },
      "version": 2
    },
    "permissions": {
      "canReply": true,
      "canApprove": true,
      "canEditDraft": true,
      "canRejectDraft": true,
      "canChooseNoResponse": true,
      "canTakeOver": true,
      "canReturnToAgent": false,
      "canPause": true,
      "canResume": false,
      "canClose": true,
      "canBlock": true,
      "canEditMemory": false
    }
  }
}
```

### 11.2. История сообщений с пагинацией

Если лента большая, сообщения загружаются отдельно:

```http
GET /api/v1/conversations/{conversationId}/messages?before=<messageId>&limit=50
```

### 11.3. Получить операционное объяснение ответа

```http
GET /api/v1/drafts/{draftId}/explanation
GET /api/v1/messages/{messageId}/explanation
```

---

## 12. Draft Actions API

### 12.1. Подтвердить draft

```http
POST /api/v1/drafts/{draftId}/approve
Idempotency-Key: <unique-key>
```

Запрос:

```json
{
  "expectedDraftVersion": 2,
  "expectedConversationVersion": 37
}
```

Ответ:

```json
{
  "data": {
    "draftId": "drf_123",
    "status": "approved",
    "sendJobId": "job_123",
    "conversationStatus": "agent_active",
    "conversationVersion": 38
  }
}
```

`approved` не означает, что Telegram уже подтвердил отправку. Фактическое состояние приходит через REST и realtime.

### 12.2. Изменить и подтвердить draft

```http
POST /api/v1/drafts/{draftId}/edit-and-approve
Idempotency-Key: <unique-key>
```

Запрос:

```json
{
  "textParts": [
    {
      "text": "Привет",
      "order": 1,
      "delayMs": 0
    },
    {
      "text": "Тут цена зависит от объема",
      "order": 2,
      "delayMs": 900
    },
    {
      "text": "Расскажи, что конкретно нужно автоматизировать",
      "order": 3,
      "delayMs": 1000
    }
  ],
  "feedback": "Убрал формальную формулировку",
  "saveAsTrainingExample": true,
  "expectedDraftVersion": 2,
  "expectedConversationVersion": 37
}
```

Ответ:

```json
{
  "data": {
    "draftId": "drf_123",
    "status": "edited",
    "correction": {
      "id": "cor_123",
      "provenance": "human_correction",
      "operatorId": "usr_123",
      "savedAsTrainingExample": true
    },
    "sendJobId": "job_124",
    "conversationVersion": 38
  }
}
```

### 12.3. Отклонить draft

```http
POST /api/v1/drafts/{draftId}/reject
```

```json
{
  "reasonCode": "wrong_tone",
  "comment": "Слишком формально для этого контакта",
  "saveAsNegativeExample": true,
  "expectedDraftVersion": 2
}
```

### 12.4. Пометить draft устаревшим вручную

```http
POST /api/v1/drafts/{draftId}/invalidate
```

Используется только для разрешенных операторских или системных сценариев.

### 12.5. Ошибка stale draft

```http
409 Conflict
```

```json
{
  "error": {
    "code": "DRAFT_STALE",
    "message": "Draft became stale after a new incoming message",
    "details": {
      "draftId": "drf_123",
      "staleReason": "new_incoming_message",
      "newMessageId": "msg_456",
      "conversationVersion": 38
    },
    "retryable": false
  }
}
```

Frontend после такой ошибки должен:

- прекратить редактирование старого draft;
- пометить его как stale;
- запретить отправку;
- загрузить актуальный диалог;
- показать новое входящее сообщение;
- ожидать новый draft или предложить ручной ответ.

---

## 13. Conversation Actions API

### 13.1. Выбрать `no response`

```http
POST /api/v1/conversations/{conversationId}/no-response
```

```json
{
  "reasonCode": "acknowledgement_not_required",
  "comment": null,
  "expectedConversationVersion": 37
}
```

Решение сохраняется как полноценное действие и попадает в аудит и аналитику.

### 13.2. Human takeover

```http
POST /api/v1/conversations/{conversationId}/takeover
```

```json
{
  "reason": "operator_decision",
  "comment": "Нужно лично обсудить стоимость",
  "expectedConversationVersion": 37
}
```

Ответ:

```json
{
  "data": {
    "conversationId": "cnv_123",
    "status": "human_takeover",
    "cancelledDraftIds": ["drf_123"],
    "cancelledSendJobIds": [],
    "typingStopped": true,
    "takenOverBy": {
      "id": "usr_123",
      "name": "Алексей"
    },
    "takenOverAt": "2026-07-29T10:10:00.000Z",
    "version": 38
  }
}
```

После takeover backend обязан:

- остановить typing;
- пометить pending drafts устаревшими или отмененными;
- отменить неотправленные части multi-bubble;
- запретить новые автоматические отправки;
- сохранить действие в аудите.

### 13.3. Вернуть диалог агенту

```http
POST /api/v1/conversations/{conversationId}/return-to-agent
```

```json
{
  "agentId": "agt_123",
  "approvalMode": "manual",
  "comment": "Можно продолжать",
  "expectedConversationVersion": 38
}
```

Возврат управления всегда является отдельным явным действием.

### 13.4. Пауза диалога

```http
POST /api/v1/conversations/{conversationId}/pause
```

### 13.5. Возобновить диалог

```http
POST /api/v1/conversations/{conversationId}/resume
```

### 13.6. Закрыть диалог

```http
POST /api/v1/conversations/{conversationId}/close
```

### 13.7. Заблокировать автоматизацию

```http
POST /api/v1/conversations/{conversationId}/block
```

### 13.8. Разблокировать автоматизацию

```http
POST /api/v1/conversations/{conversationId}/unblock
```

---

## 14. Ручная отправка сообщения

### 14.1. Отправить сообщение оператором

```http
POST /api/v1/conversations/{conversationId}/messages
Idempotency-Key: <unique-key>
```

Запрос:

```json
{
  "content": {
    "type": "text",
    "text": "Привет. Я подключился к диалогу, сейчас уточню детали."
  },
  "replyToMessageId": "msg_123",
  "expectedConversationVersion": 38
}
```

Ответ:

```json
{
  "data": {
    "message": {
      "id": "msg_789",
      "conversationId": "cnv_123",
      "telegramMessageId": null,
      "provenance": "human_operator",
      "author": {
        "id": "usr_123",
        "displayName": "Алексей"
      },
      "content": {
        "type": "text",
        "text": "Привет. Я подключился к диалогу, сейчас уточню детали."
      },
      "deliveryStatus": "queued",
      "replyToMessageId": "msg_123",
      "editedAt": null,
      "deletedAt": null,
      "createdAt": "2026-07-29T10:12:00.000Z",
      "agentId": null,
      "agentVersion": null,
      "model": null,
      "configurationFingerprint": null,
      "correctionOfMessageId": null,
      "sourceDraftId": null
    },
    "sendJobId": "job_789"
  }
}
```

После реальной отправки `telegramMessageId` и `deliveryStatus` обновляются.

### 14.2. Отредактировать отправленное сообщение

```http
PATCH /api/v1/messages/{messageId}
```

Операция доступна только если Telegram и права пользователя позволяют редактирование.

### 14.3. Удалить отправленное сообщение

```http
DELETE /api/v1/messages/{messageId}
```

Операция доступна только если Telegram и правила продукта позволяют удаление.

### 14.4. Поставить реакцию

```http
POST /api/v1/messages/{messageId}/reactions
```

```json
{
  "reaction": "👍",
  "expectedConversationVersion": 38
}
```

---

## 15. Emergency Stop API

### 15.1. Глобальная остановка

```http
POST /api/v1/system/emergency-stop
```

```json
{
  "reason": "unexpected_automatic_sends",
  "comment": "Проверяем возможный сбой",
  "confirmation": "STOP_ALL_AUTOMATION"
}
```

Ответ:

```json
{
  "data": {
    "status": "stopped",
    "stoppedAt": "2026-07-29T10:15:00.000Z",
    "stoppedBy": {
      "id": "usr_admin_123",
      "name": "Admin"
    },
    "cancelledJobs": 14,
    "affectedAccounts": 3
  }
}
```

### 15.2. Возобновить систему

```http
POST /api/v1/system/emergency-resume
```

Возобновление должно требовать отдельного права и явного подтверждения.

### 15.3. Состояние системы

```http
GET /api/v1/system/state
```

```json
{
  "data": {
    "automationEnabled": false,
    "emergencyStopActive": true,
    "reason": "unexpected_automatic_sends",
    "stoppedAt": "2026-07-29T10:15:00.000Z"
  }
}
```

### 15.4. Остановка отдельного аккаунта

Используется endpoint паузы Telegram-аккаунта.

### 15.5. Остановка отдельного агента

Используется endpoint паузы агента.

### 15.6. Остановка отдельного диалога

Используется endpoint паузы или takeover диалога.

---

## 16. Audit Log API

### 16.1. Получить события аудита

```http
GET /api/v1/audit-events
```

Фильтры:

- `actorId`;
- `action`;
- `entityType`;
- `entityId`;
- `dateFrom`;
- `dateTo`;
- `limit`;
- `cursor`.

Пример:

```http
GET /api/v1/audit-events?entityType=conversation&entityId=cnv_123&limit=50
```

Ответ:

```json
{
  "data": [
    {
      "id": "aud_123",
      "action": "conversation.takeover",
      "actor": {
        "type": "internal_user",
        "id": "usr_123",
        "displayName": "Алексей"
      },
      "entity": {
        "type": "conversation",
        "id": "cnv_123"
      },
      "changes": {
        "before": {
          "status": "approval_required"
        },
        "after": {
          "status": "human_takeover"
        }
      },
      "metadata": {
        "cancelledDraftIds": ["drf_123"],
        "reason": "operator_decision"
      },
      "createdAt": "2026-07-29T10:10:00.000Z"
    }
  ]
}
```

Аудит защищен от обычного редактирования через панель.

---

## 17. System Health API

### 17.1. Состояние компонентов

```http
GET /api/v1/system/health
```

Пример ответа:

```json
{
  "data": {
    "status": "degraded",
    "components": [
      {
        "id": "telegram",
        "name": "Telegram connections",
        "status": "healthy",
        "checkedAt": "2026-07-29T10:20:00.000Z",
        "details": {}
      },
      {
        "id": "llm-provider",
        "name": "LLM provider",
        "status": "degraded",
        "checkedAt": "2026-07-29T10:20:00.000Z",
        "details": {
          "reason": "increased_latency"
        }
      },
      {
        "id": "database",
        "name": "Database",
        "status": "healthy",
        "checkedAt": "2026-07-29T10:20:00.000Z",
        "details": {}
      }
    ],
    "queues": {
      "pendingJobs": 12,
      "delayedJobs": 2,
      "failedJobs": 1,
      "stuckJobs": 0
    },
    "applicationVersion": "AAA.2"
  }
}
```

### 17.2. Безопасный retry задачи

```http
POST /api/v1/system/jobs/{jobId}/retry
```

Retry должен быть идемпотентным и не создавать дубликат отправки.

---

## 18. Realtime API

### 18.1. Транспорт

Возможные варианты:

- WebSocket;
- Server-Sent Events.

Выбранный вариант должен поддерживать:

- авторизацию;
- reconnect;
- последовательность событий;
- обнаружение пропусков;
- подписку на ограниченные каналы;
- фильтрацию данных по правам пользователя.

Пример WebSocket URL:

```text
wss://api.example.com/api/v1/realtime
```

### 18.2. Подписка

```json
{
  "type": "subscribe",
  "channels": [
    "system",
    "accounts",
    "conversations"
  ]
}
```

### 18.3. Общий формат события

```ts
export interface RealtimeEvent<T = unknown> {
  eventId: string;
  sequence: number;
  type: RealtimeEventType;
  occurredAt: string;
  data: T;
}
```

### 18.4. Типы realtime-событий

```ts
export type RealtimeEventType =
  | "message.received"
  | "message.queued"
  | "message.sent"
  | "message.failed"
  | "message.edited"
  | "message.deleted"
  | "draft.created"
  | "draft.updated"
  | "draft.stale"
  | "draft.rejected"
  | "typing.started"
  | "typing.stopped"
  | "conversation.updated"
  | "conversation.takeover"
  | "conversation.returned_to_agent"
  | "account.connected"
  | "account.disconnected"
  | "account.error"
  | "system.emergency_stopped"
  | "system.emergency_resumed"
  | "system.health_changed";
```

### 18.5. Новое входящее сообщение

```json
{
  "eventId": "evt_1001",
  "sequence": 18841,
  "type": "message.received",
  "occurredAt": "2026-07-29T10:20:00.000Z",
  "data": {
    "conversationId": "cnv_123",
    "message": {
      "id": "msg_456",
      "conversationId": "cnv_123",
      "telegramMessageId": "1454",
      "provenance": "contact",
      "author": {
        "id": "cnt_123",
        "displayName": "Денис"
      },
      "content": {
        "type": "text",
        "text": "Мне нужен бот для заявок"
      },
      "deliveryStatus": "sent",
      "replyToMessageId": null,
      "editedAt": null,
      "deletedAt": null,
      "createdAt": "2026-07-29T10:20:00.000Z",
      "agentId": null,
      "agentVersion": null,
      "model": null,
      "configurationFingerprint": null,
      "correctionOfMessageId": null,
      "sourceDraftId": null
    },
    "conversationVersion": 39
  }
}
```

### 18.6. Draft стал stale

```json
{
  "eventId": "evt_1002",
  "sequence": 18842,
  "type": "draft.stale",
  "occurredAt": "2026-07-29T10:20:00.050Z",
  "data": {
    "conversationId": "cnv_123",
    "draftId": "drf_123",
    "reason": "new_incoming_message",
    "conversationVersion": 39
  }
}
```

### 18.7. Новый draft

```json
{
  "eventId": "evt_1003",
  "sequence": 18843,
  "type": "draft.created",
  "occurredAt": "2026-07-29T10:20:03.000Z",
  "data": {
    "conversationId": "cnv_123",
    "draftId": "drf_456",
    "status": "pending_approval",
    "conversationVersion": 40
  }
}
```

### 18.8. Typing

```json
{
  "eventId": "evt_1004",
  "sequence": 18844,
  "type": "typing.started",
  "occurredAt": "2026-07-29T10:20:05.000Z",
  "data": {
    "conversationId": "cnv_123",
    "source": "agent",
    "agentId": "agt_123"
  }
}
```

### 18.9. Пропущенная последовательность

Если frontend получил `sequence: 18850` после `18844`, он должен считать состояние потенциально неполным и выполнить REST refresh затронутых сущностей.

---

## 19. Error Codes

Минимальный набор кодов:

| Code | HTTP | Значение |
|---|---:|---|
| `VALIDATION_ERROR` | 422 | Некорректные поля запроса |
| `UNAUTHORIZED` | 401 | Нет действующей авторизации |
| `FORBIDDEN` | 403 | Недостаточно прав |
| `NOT_FOUND` | 404 | Объект не найден |
| `VERSION_CONFLICT` | 409 | Версия объекта изменилась |
| `DRAFT_STALE` | 409 | Draft устарел |
| `DRAFT_ALREADY_PROCESSED` | 409 | Draft уже подтвержден, отклонен или отменен |
| `ASSIGNMENT_CONFLICT` | 409 | Конфликт назначений |
| `CONVERSATION_TAKEN_OVER` | 409 | Диалог уже перехвачен человеком |
| `CONVERSATION_PAUSED` | 409 | Диалог находится на паузе |
| `CONVERSATION_BLOCKED` | 409 | Автоматизация запрещена |
| `ACCOUNT_DISCONNECTED` | 409 | Telegram-аккаунт отключен |
| `ACCOUNT_AUTHORIZATION_REQUIRED` | 409 | Требуется повторная авторизация |
| `EMERGENCY_STOP_ACTIVE` | 409 | Глобальная автоматизация остановлена |
| `MESSAGE_SEND_FAILED` | 502 | Ошибка Telegram при отправке |
| `PROVIDER_UNAVAILABLE` | 503 | LLM provider недоступен |
| `RATE_LIMITED` | 429 | Превышен лимит |
| `INTERNAL_ERROR` | 500 | Необработанная внутренняя ошибка |

Frontend не должен принимать решения по тексту `message`. Логика строится по стабильному `code`.

---

## 20. Permissions

Backend проверяет права на каждом endpoint.

Frontend использует permissions только для отображения интерфейса.

Рекомендуемые permission keys:

```text
dashboard.read
accounts.read
accounts.create
accounts.authorize
accounts.pause
accounts.delete
agents.read
agents.create
agents.edit
agents.activate
agents.archive
assignments.read
assignments.manage
conversations.read
conversations.reply
conversations.takeover
conversations.return_to_agent
conversations.pause
conversations.close
drafts.approve
drafts.edit
drafts.reject
memory.read
memory.edit
knowledge.read
knowledge.edit
training.read
training.manage
audit.read
system.health.read
system.jobs.retry
system.emergency_stop
system.emergency_resume
users.read
users.manage
```

Базовое соответствие ролей:

| Роль | Назначение |
|---|---|
| `administrator` | Полный доступ |
| `operator` | Диалоги, ручные сообщения, approval, takeover |
| `trainer` | Исправления, training examples, style, memory |
| `observer` | Только чтение разрешенных разделов |

Финальная матрица прав должна храниться и проверяться на backend.

---

## 21. Idempotency

### 21.1. Обязательные endpoint'ы

`Idempotency-Key` обязателен как минимум для:

- ручной отправки сообщения;
- approve draft;
- edit-and-approve;
- retry отправки;
- emergency stop;
- операций Telegram, которые могут быть повторены после timeout.

### 21.2. Повторный запрос

При повторе с тем же ключом и тем же payload backend возвращает исходный результат.

При повторе с тем же ключом и другим payload backend возвращает `409 IDEMPOTENCY_KEY_REUSED`.

### 21.3. Срок хранения

Срок хранения idempotency records должен быть закреплен отдельно. Рекомендуемый минимум для отправок - 24 часа.

---

## 22. Version Conflict

Пример ответа:

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "Conversation has changed",
    "details": {
      "entityType": "conversation",
      "entityId": "cnv_123",
      "expectedVersion": 37,
      "actualVersion": 39
    },
    "retryable": false
  }
}
```

Frontend должен загрузить актуальную сущность и не повторять команду автоматически без проверки пользователем.

---

## 23. Безопасность

Контракт должен соблюдать следующие правила:

- секреты не возвращаются после сохранения;
- Telegram session data не передается frontend;
- номера телефонов маскируются;
- полный текст приватных переписок не попадает в технические логи по умолчанию;
- права проверяются сервером;
- audit events нельзя редактировать обычным endpoint'ом;
- экспорт диагностики по умолчанию не содержит приватный контент;
- ответы API не раскрывают внутренние stack traces;
- refresh и access tokens хранятся согласно выбранной безопасной модели;
- все изменения критических настроек фиксируются в аудите.

---

## 24. Минимальные endpoint'ы первой реализации

```text
Auth
POST   /auth/login
POST   /auth/logout
POST   /auth/refresh
GET    /auth/me

Telegram accounts
GET    /telegram-accounts
POST   /telegram-accounts
GET    /telegram-accounts/:id
POST   /telegram-accounts/:id/authorization/code
POST   /telegram-accounts/:id/authorization/password
POST   /telegram-accounts/:id/pause
POST   /telegram-accounts/:id/resume
POST   /telegram-accounts/:id/reconnect
POST   /telegram-accounts/:id/disconnect
DELETE /telegram-accounts/:id

Agents
GET    /agents
POST   /agents
GET    /agents/:id
PATCH  /agents/:id
PATCH  /agents/:id/identity
PATCH  /agents/:id/goals
PATCH  /agents/:id/personality
PATCH  /agents/:id/voice
PATCH  /agents/:id/telegram-behavior
PATCH  /agents/:id/handoff-safety
POST   /agents/:id/activate
POST   /agents/:id/pause
POST   /agents/:id/archive
POST   /agents/:id/duplicate
GET    /agents/:id/versions
POST   /agents/:id/versions/:versionId/rollback

Assignments
GET    /assignments
POST   /assignments
PATCH  /assignments/:id
DELETE /assignments/:id

Conversations
GET    /conversations
GET    /conversations/:id
GET    /conversations/:id/messages
POST   /conversations/:id/messages
POST   /conversations/:id/no-response
POST   /conversations/:id/takeover
POST   /conversations/:id/return-to-agent
POST   /conversations/:id/pause
POST   /conversations/:id/resume
POST   /conversations/:id/close
POST   /conversations/:id/block
POST   /conversations/:id/unblock

Messages
PATCH  /messages/:id
DELETE /messages/:id
POST   /messages/:id/reactions
GET    /messages/:id/explanation

Drafts
POST   /drafts/:id/approve
POST   /drafts/:id/edit-and-approve
POST   /drafts/:id/reject
POST   /drafts/:id/invalidate
GET    /drafts/:id/explanation

Audit
GET    /audit-events

System
GET    /system/state
GET    /system/health
POST   /system/jobs/:jobId/retry
POST   /system/emergency-stop
POST   /system/emergency-resume
```

---

## 25. Рекомендуемая структура контрактов в репозитории

```text
/contracts
├── API_CONTRACT.md
├── openapi.yaml
├── schemas
│   ├── common.yaml
│   ├── user.yaml
│   ├── telegram-account.yaml
│   ├── agent.yaml
│   ├── assignment.yaml
│   ├── contact.yaml
│   ├── conversation.yaml
│   ├── message.yaml
│   ├── draft.yaml
│   ├── audit-event.yaml
│   ├── system-health.yaml
│   └── error.yaml
└── events
    └── realtime-events.yaml
```

`API_CONTRACT.md` объясняет правила и сценарии.

`openapi.yaml` становится машинно проверяемым источником схем REST API.

`realtime-events.yaml` фиксирует схемы realtime-событий.

Из OpenAPI желательно генерировать:

- TypeScript types;
- frontend API client;
- backend DTO;
- request validation;
- response validation;
- документацию endpoint'ов.

---

## 26. Acceptance Criteria контракта

Контракт считается пригодным для первой интеграции, когда выполняются следующие условия:

- frontend может отобразить список аккаунтов, агентов и диалогов на моках из зафиксированных схем;
- backend возвращает те же enum и названия полей;
- каждый message имеет provenance;
- draft имеет status, stale state и version;
- stale draft нельзя подтвердить;
- approve и ручная отправка идемпотентны;
- takeover останавливает pending automation;
- return-to-agent является отдельной командой;
- assignment conflict возвращается как явная ошибка;
- backend возвращает permissions для операционных экранов;
- все критические действия создают audit event;
- WebSocket или SSE сообщает о новых сообщениях, draft и изменениях статуса;
- после пропуска realtime-события frontend может восстановить состояние через REST;
- отправка сообщения не считается успешной до подтверждения Telegram;
- повторный event или retry не создает дубликат;
- emergency stop запрещает новые автоматические отправки.

---

## 27. Решения, которые нужно закрепить до реализации

Следующие пункты пока являются архитектурными решениями команды, а не завершенной частью контракта:

1. Формат ID - UUIDv7, ULID или другой.
2. WebSocket или SSE.
3. Способ хранения access и refresh tokens.
4. Полный Telegram authorization flow.
5. Формат загрузки и получения media.
6. Срок хранения idempotency records.
7. Максимальные размеры страниц и payload.
8. Полная permission matrix.
9. Поведение при смене агента в активном диалоге.
10. Поведение с уже запланированными multi-bubble частями при частичном сбое.
11. Гарантии порядка realtime-событий между несколькими workers.
12. Политика retry для Telegram и LLM provider.
13. Формат версии продукта и API.
14. Retention policy для сообщений, памяти, аудита и технических логов.
15. Правила удаления Telegram-аккаунта и связанных данных.

После принятия решения пункт переносится в основной раздел документа и перестает считаться открытым.

---

## 28. Правила изменения контракта

- Несовместимые изменения требуют новой major-версии API.
- Новые необязательные поля могут добавляться в текущую версию.
- Новые enum-значения могут добавляться, поэтому клиенты должны иметь fallback.
- Переименование поля считается несовместимым изменением.
- Изменение значения или смысла существующего enum считается несовместимым изменением.
- Удаление endpoint или поля требует периода deprecation.
- Изменения контракта проходят review frontend и backend разработчиками.
- Изменение считается принятым после обновления документа, OpenAPI-схемы и тестов контракта.

---

## 29. Итог

Первый API должен обеспечивать не просто CRUD для аккаунтов и агентов, а безопасное управление живым Telegram-диалогом.

Критическими свойствами контракта являются:

- неизменяемый provenance;
- stale draft protection;
- идемпотентная отправка;
- optimistic concurrency;
- явный human takeover;
- явный возврат агенту;
- audit trail;
- server-side permissions;
- realtime-обновления;
- REST recovery;
- emergency stop.

Без этих свойств frontend может выглядеть завершенным, но не сможет надежно управлять реальным коммуникационным агентом.

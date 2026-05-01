# Admin Onboarding Wizard — Design Spec

**Date:** 2026-05-01
**Status:** Approved

## Overview

When the platform is first deployed, the first admin user needs guided configuration for:
1. **LLM/Model Provider** (required) — without this, no AI features work
2. **LDAP Integration** (optional) — domain account login
3. **Message Channels** (optional) — alert notifications

A full-screen wizard intercepts admin login when no model provider is configured, walking them through setup with login-page-style fade-in/out animations and a tech aesthetic.

## Trigger Logic

### Backend
- `GET /api/v1/auth/me` returns `setup_required: bool` in response
- `setup_required` = `true` when: user has `admin` role AND `SELECT COUNT(*) FROM model_providers WHERE is_active = true` = 0
- Non-admin users never see the wizard (`setup_required` always `false` for them)
- Once at least one active model provider exists, the wizard is permanently disabled

### Frontend
- On login success, check `setup_required` from `/auth/me` response
- If `true`, navigate to `/onboarding` (full-screen route, no MainLayout)
- Route guard: if `setup_required` is true and current path is not `/onboarding`, redirect
- After setup complete (Step 1 has at least 1 model provider), redirect to `/` (dashboard)

## User Flow

```
Login → /auth/me { setup_required: true } → redirect /onboarding

Step 1: LLM (required) ──→ Step 2: LDAP (optional) ──→ Step 3: Channel (optional)
       │                          │                          │
       ├── "进入平台"              ├── "跳过"                  ├── "跳过"
       └── "下一步"                └── "下一步"                └── "进入平台"
```

### Navigation Rules
- Step 1 (required): Once at least 1 active model provider added, show both "进入平台" (text link) and "下一步" (primary button)
- Step 2 (optional): Show "开始配置" (primary) + "跳过，以后配置" (link) + "上一步" (button)
- Step 3 (optional): Show "添加渠道" (primary) + "跳过，以后配置" (link) + "上一步" (button) + "进入平台" (text link)
- Can navigate backward freely (1-2-3)
- Can only advance from Step 1 after adding a model provider

## Architecture

### New Files

```
web/src/features/onboarding/
├── OnboardingPage.tsx        -- Wizard shell: full-screen layout, progress bar, phase animation
├── Step1ModelConfig.tsx      -- Model provider form + list (reuses POST/PATCH /model-providers)
├── Step2Ldap.tsx             -- LDAP entry card + inline LdapWizard
├── Step3Channels.tsx         -- Channel entry card + inline ChannelsPage form
└── index.ts                  -- Re-export

web/src/components/ui/
└── TechBackground.tsx        -- Shared background: gradient + dot pattern (extracted from LoginPage)
```

### Modified Files

| File | Change |
|------|--------|
| `web/src/services/auth.ts` | `getMe()` return type includes `setup_required: boolean` |
| `web/src/stores/authStore.ts` | Store `setupRequired` flag |
| `web/src/App.tsx` or router | Add `/onboarding` route + guard logic |
| `server/src/api/control/auth.py` | `GET /me` adds `setup_required` check |
| `server/src/schemas/user.py` | `UserMeOut` adds `setup_required: bool` |

### Component Tree

```
OnboardingPage
├── TechBackground (gradient + dots, shared with LoginPage)
├── ProgressIndicator (3-step circles with checkmark for completed)
├── Card (white, rounded, shadow)
│   ├── Step1ModelConfig
│   │   ├── Form (dual-column grid: name, provider_type, model_name, model_type, api_key, base_url)
│   │   ├── TestConnectionButton
│   │   ├── ProviderList (added providers with status dots)
│   │   └── ActionBar (进入平台 + 下一步)
│   ├── Step2Ldap
│   │   ├── IntroCard (icon, description, benefits)
│   │   ├── LdapWizard (reused from system/LdapWizard, rendered inline)
│   │   └── ActionBar (上一步 + 跳过 + 下一步)
│   └── Step3Channels
│       ├── IntroCard (icon, description, channel type tabs)
│       ├── ChannelForm (reused from channels/ChannelsPage)
│       └── ActionBar (上一步 + 跳过 + 进入平台)
└── Footer (version, powered by)
```

## Visual Design

### Animation (matching LoginPage)
- CSS keyframes: `onboardingFadeIn`, `onboardingCardFadeIn`, `onboardingBgFadeIn`
- 3-phase state machine: `entering` (1000ms) to `active` to `exiting` (800ms)
- Background opacity animation + card translateY/scale animation
- Step transitions: 300ms crossfade between step content

### Aesthetic
- Background: `linear-gradient(135deg, #EEF2FF 0%, #F9FAFB 50%, #ECFDF5 100%)`
- Dot pattern overlay: `radial-gradient(circle, rgba(37,99,235,0.06) 1px, transparent 1px)` at 32px spacing
- Cards: `border-radius: 16px`, `box-shadow: 0 4px 24px rgba(0,0,0,0.06)`
- Progress steps: Ant Design `Steps` with custom styling or inline circles
- Typography: follow Ant Design token system

## API Contracts

### GET /api/v1/auth/me (modified)

Response adds one field:

```json
{
  "id": "uuid",
  "username": "admin",
  "email": "admin@example.com",
  "roles": [{"name": "admin", "permissions": [...]}],
  "default_space_id": "uuid",
  "setup_required": true
}
```

### Existing APIs Reused

| Endpoint | Used By | Purpose |
|----------|---------|---------|
| `POST /model-providers` | Step 1 | Create model provider |
| `POST /model-providers/{id}/test` | Step 1 | Test connection |
| `GET /model-providers` | Step 1 | List added providers |
| `DELETE /model-providers/{id}` | Step 1 | Remove a provider |
| `PUT /system/ldap` | Step 2 | Save LDAP config |
| `POST /system/ldap/test` | Step 2 | Test LDAP connection |
| `POST /channels` | Step 3 | Create notification channel |
| `POST /channels/test` | Step 3 | Test channel sending |

## Edge Cases

1. **User refreshes during onboarding**: Page re-checks `setup_required`. If suddenly false (another admin configured models), redirect to `/`.
2. **User adds model in Step 1, then removes it**: Step 2/3 should still be accessible (once earned, keep access). But "进入平台" should be hidden if no model providers remain.
3. **Admin logs out mid-wizard**: Next login re-enters wizard at Step 1 (no state persistence — always start fresh).
4. **Multiple admins**: If admin A completes setup, admin B who logs in later sees `setup_required: false` and goes straight to dashboard.
5. **API key visible in form**: Use `Input.Password` for `api_key` field. Existing providers show masked key.

## Implementation Order

1. Backend: `GET /auth/me` — add `setup_required`
2. Frontend: `TechBackground.tsx` — extract shared background from LoginPage
3. Frontend: `OnboardingPage.tsx` — wizard shell with phase animation
4. Frontend: `Step1ModelConfig.tsx` — model provider form
5. Frontend: `Step2Ldap.tsx` — LDAP entry + inline wizard
6. Frontend: `Step3Channels.tsx` — channel entry + form
7. Frontend: Router + auth store updates for guard

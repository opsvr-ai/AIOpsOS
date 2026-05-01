import { useChatStore } from '@/stores/chatStore';
import type { ChatMessage } from '@/stores/chatStore';
import AgentAvatar from './AgentAvatar';
import TextBubble from './TextBubble';
import { IntentCard, PlanCard } from '../ChatCards';
import InteractiveFormCard from './InteractiveFormCard';
import SecurityConfirmCard from './SecurityConfirmCard';

function MsgWrapper({ children }: { children: React.ReactNode }) {
  return (
    <div className="msg-enter" style={{ padding: '4px 16px' }}>
      {children}
    </div>
  );
}

function MsgRow({
  children,
  agentState,
  isUser,
}: {
  children: React.ReactNode;
  agentState?: 'idle' | 'thinking' | 'planning' | 'executing';
  isUser?: boolean;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        maxWidth: 900,
        margin: '0 auto',
        width: '100%',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
      }}
    >
      {!isUser && agentState && <AgentAvatar state={agentState} />}
      <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
    </div>
  );
}

export default function Bubble({
  msg,
  isLatestUser,
  agentState,
  userState,
  onEdit,
  onRegenerate,
  onFormSubmit,
  onInterruptRespond,
}: {
  msg: ChatMessage;
  isLatestUser: boolean;
  agentState: 'idle' | 'thinking' | 'planning' | 'executing';
  userState: 'sleeping' | 'waiting' | 'reading';
  onEdit: () => void;
  onRegenerate: () => void;
  onFormSubmit?: (formId: string, values: Record<string, unknown>) => void;
  onInterruptRespond?: (interruptId: string, approved: boolean) => void;
}) {
  if (msg.type === 'intent') {
    return (
      <MsgWrapper>
        <MsgRow agentState="thinking">
          <IntentCard message={msg.content} />
        </MsgRow>
      </MsgWrapper>
    );
  }

  if (msg.type === 'interactive_form' && msg.formData) {
    return (
      <MsgWrapper>
        <MsgRow agentState="thinking">
          <InteractiveFormCard
            formData={msg.formData}
            onSubmit={(formId, values) => onFormSubmit?.(formId, values)}
            submitted={!!msg.formSubmitted}
          />
        </MsgRow>
      </MsgWrapper>
    );
  }

  if (msg.type === 'interrupt' && msg.interruptData) {
    if (msg.interruptData.type === 'approval') {
      return (
        <MsgWrapper>
          <MsgRow agentState="thinking">
            <SecurityConfirmCard
              interruptData={msg.interruptData}
              resolved={!!msg.interruptResolved}
              onRespond={(approved) =>
                onInterruptRespond?.(msg.interruptData!.interrupt_id, approved)
              }
            />
          </MsgRow>
        </MsgWrapper>
      );
    }
    if (msg.interruptData.type === 'form') {
      const fields = msg.interruptData.data.fields || [];
      const formDef = {
        type: 'form' as const,
        form_id: msg.interruptData.interrupt_id,
        title: msg.interruptData.data.title || '参数填写',
        description: msg.interruptData.data.description,
        fields: fields.map(
          (f: {
            key: string;
            label: string;
            type: string;
            placeholder?: string;
            required?: boolean;
            options?: { value: string; label: string }[];
          }) => ({
            key: f.key,
            label: f.label,
            type: f.type as 'text' | 'textarea' | 'checkbox' | 'radio',
            placeholder: f.placeholder,
            required: f.required,
            options: f.options,
          }),
        ),
        submit_label: '提交',
      };
      return (
        <MsgWrapper>
          <MsgRow agentState="thinking">
            <InteractiveFormCard
              formData={formDef}
              onSubmit={(formId, values) => {
                const store = useChatStore.getState();
                const msgs = store.messages;
                for (let i = msgs.length - 1; i >= 0; i--) {
                  if (
                    msgs[i].type === 'interrupt' &&
                    msgs[i].interruptData?.interrupt_id === formId
                  ) {
                    useChatStore.setState({
                      messages: msgs.map((m, idx) =>
                        idx === i ? { ...m, interruptResolved: true, formSubmitted: true } : m,
                      ),
                    });
                    break;
                  }
                }
                onFormSubmit?.(formId, values);
              }}
              submitted={!!msg.interruptResolved}
            />
          </MsgRow>
        </MsgWrapper>
      );
    }
  }

  if (msg.type === 'plan' && msg.planSteps) {
    return (
      <MsgWrapper>
        <MsgRow agentState="planning">
          <PlanCard steps={msg.planSteps} execResults={msg.execResults ?? []} />
        </MsgRow>
      </MsgWrapper>
    );
  }

  return (
    <TextBubble
      msg={msg}
      isLatestUser={isLatestUser}
      agentState={agentState}
      userState={userState}
      onEdit={onEdit}
      onRegenerate={onRegenerate}
    />
  );
}

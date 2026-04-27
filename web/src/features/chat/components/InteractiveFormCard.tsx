import { useState, useCallback, useMemo } from 'react';
import { Input, Button, Radio, Checkbox, Typography, theme, App } from 'antd';
import {
  CheckOutlined,
  ArrowRightOutlined,
  ArrowLeftOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import type { FormDefinition, FormField, FormPage } from '@/stores/chatStore';

const { TextArea } = Input;

export default function InteractiveFormCard({
  formData,
  onSubmit,
  submitted,
}: {
  formData: FormDefinition;
  onSubmit: (formId: string, values: Record<string, unknown>) => void;
  submitted: boolean;
}) {
  const { token } = theme.useToken();
  const { message } = App.useApp();

  // Normalize: pages[] or single-page from fields[]
  const pages: FormPage[] = useMemo(() => {
    if (formData.pages && formData.pages.length > 0) return formData.pages;
    return [
      { title: formData.title, description: formData.description, fields: formData.fields || [] },
    ];
  }, [formData]);

  const allFields = useMemo(() => pages.flatMap((p) => p.fields), [pages]);

  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const init: Record<string, unknown> = {};
    for (const f of allFields) {
      if (f.value !== undefined) init[f.key] = f.value;
    }
    return init;
  });
  const [pageIndex, setPageIndex] = useState(0);
  const [errors, setErrors] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  const currentPage = pages[pageIndex];
  const isFirstPage = pageIndex === 0;
  const isLastPage = pageIndex === pages.length - 1;

  const handleFieldChange = useCallback((key: string, val: unknown) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    setErrors((prev) => {
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
  }, []);

  const validatePage = useCallback(
    (pageIdx: number): boolean => {
      const missing: string[] = [];
      for (const f of pages[pageIdx].fields) {
        const visible = !f.show_when || values[f.show_when.key] === f.show_when.equals;
        if (!visible) continue;
        if (f.required && !values[f.key]) {
          missing.push(f.key);
        }
      }
      if (missing.length > 0) {
        setErrors(new Set(missing));
        message.warning('请填写必填字段');
        return false;
      }
      return true;
    },
    [pages, values, message],
  );

  const handleNext = useCallback(() => {
    if (!validatePage(pageIndex)) return;
    setPageIndex((p) => Math.min(p + 1, pages.length - 1));
  }, [pageIndex, pages.length, validatePage]);

  const handlePrev = useCallback(() => {
    setPageIndex((p) => Math.max(p - 1, 0));
  }, []);

  const handleSubmit = useCallback(() => {
    if (!validatePage(pageIndex)) return;
    setSubmitting(true);
    const finalValues = { ...values };
    if (finalValues.schedule === 'custom' && finalValues.custom_schedule) {
      finalValues.schedule = finalValues.custom_schedule;
    }
    delete finalValues.custom_schedule;
    onSubmit(formData.form_id, finalValues);
  }, [pageIndex, values, formData.form_id, onSubmit, validatePage]);

  const isFieldVisible = (f: FormField): boolean => {
    if (!f.show_when) return true;
    return values[f.show_when.key] === f.show_when.equals;
  };

  const renderField = (f: FormField) => {
    if (!isFieldVisible(f)) return null;
    const hasError = errors.has(f.key);
    const disabled = submitted;

    switch (f.type) {
      case 'text':
        return (
          <div key={f.key} className="form-field" style={{ marginBottom: 14 }}>
            <FieldLabel label={f.label} required={f.required} token={token} />
            <Input
              value={(values[f.key] as string) || ''}
              onChange={(e) => handleFieldChange(f.key, e.target.value)}
              placeholder={f.placeholder}
              disabled={disabled}
              status={hasError ? 'error' : undefined}
              style={{
                borderRadius: 8,
                fontSize: 13,
                height: 38,
                background: disabled ? token.colorFillSecondary : token.colorBgContainer,
              }}
            />
            {hasError && <FieldError token={token} />}
          </div>
        );

      case 'textarea':
        return (
          <div key={f.key} className="form-field" style={{ marginBottom: 14 }}>
            <FieldLabel label={f.label} required={f.required} token={token} />
            <TextArea
              value={(values[f.key] as string) || ''}
              onChange={(e) => handleFieldChange(f.key, e.target.value)}
              placeholder={f.placeholder}
              disabled={disabled}
              autoSize={{ minRows: 2, maxRows: 6 }}
              status={hasError ? 'error' : undefined}
              style={{
                borderRadius: 8,
                fontSize: 13,
                background: disabled ? token.colorFillSecondary : token.colorBgContainer,
                resize: 'none',
              }}
            />
            {hasError && <FieldError token={token} />}
          </div>
        );

      case 'radio':
        return (
          <div key={f.key} className="form-field" style={{ marginBottom: 14 }}>
            <FieldLabel label={f.label} required={f.required} token={token} />
            {submitted ? (
              <div style={{ fontSize: 13, color: token.colorText, padding: '4px 0' }}>
                <CheckOutlined style={{ color: token.colorSuccess, marginRight: 6 }} />
                {f.options?.find((o) => o.value === values[f.key])?.label ||
                  String(values[f.key] || '')}
              </div>
            ) : (
              <Radio.Group
                value={values[f.key]}
                onChange={(e) => handleFieldChange(f.key, e.target.value)}
                style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
              >
                {(f.options || []).map((opt) => (
                  <Radio
                    key={opt.value}
                    value={opt.value}
                    style={{
                      padding: '10px 14px',
                      borderRadius: 10,
                      border: `1px solid ${
                        values[f.key] === opt.value
                          ? token.colorPrimary
                          : token.colorBorderSecondary
                      }`,
                      background:
                        values[f.key] === opt.value ? token.colorPrimaryBg : token.colorBgContainer,
                      margin: 0,
                      transition: 'border-color 0.2s, background 0.2s',
                      cursor: 'pointer',
                    }}
                  >
                    <span style={{ fontSize: 13 }}>{opt.label}</span>
                  </Radio>
                ))}
              </Radio.Group>
            )}
            {hasError && <FieldError token={token} />}
          </div>
        );

      case 'checkbox':
        return (
          <div key={f.key} className="form-field" style={{ marginBottom: 14 }}>
            <FieldLabel label={f.label} required={f.required} token={token} />
            {submitted ? (
              <div style={{ fontSize: 13, color: token.colorText, padding: '4px 0' }}>
                <CheckOutlined style={{ color: token.colorSuccess, marginRight: 6 }} />
                {(values[f.key] as string[])?.join(', ') || 'None'}
              </div>
            ) : (
              <Checkbox.Group
                value={(values[f.key] as string[]) || []}
                onChange={(vals) => handleFieldChange(f.key, vals)}
                style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
              >
                {(f.options || []).map((opt) => (
                  <Checkbox
                    key={opt.value}
                    value={opt.value}
                    style={{
                      padding: '8px 14px',
                      borderRadius: 10,
                      border: `1px solid ${
                        ((values[f.key] as string[]) || []).includes(opt.value)
                          ? token.colorPrimary
                          : token.colorBorderSecondary
                      }`,
                      background: ((values[f.key] as string[]) || []).includes(opt.value)
                        ? token.colorPrimaryBg
                        : token.colorBgContainer,
                      margin: 0,
                      transition: 'border-color 0.2s, background 0.2s',
                    }}
                  >
                    <span style={{ fontSize: 13 }}>{opt.label}</span>
                  </Checkbox>
                ))}
              </Checkbox.Group>
            )}
          </div>
        );

      default:
        return null;
    }
  };

  const totalSteps = pages.length;
  const currentStep = pageIndex + 1;

  return (
    <div
      className={`interactive-form-card msg-enter ${submitted ? 'form-submitted' : ''}`}
      style={{
        background: token.colorBgElevated ?? token.colorBgContainer,
        border: `1px solid ${submitted ? token.colorSuccessBorder : token.colorBorderSecondary}`,
        borderRadius: 14,
        overflow: 'hidden',
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      }}
    >
      {/* Progress indicator */}
      {totalSteps > 1 && (
        <div
          className="form-progress"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 0,
            padding: '14px 20px 0',
          }}
        >
          {Array.from({ length: totalSteps }, (_, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center' }}>
              <div
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  background: i + 1 <= currentStep ? token.colorPrimary : token.colorFillSecondary,
                  color: i + 1 <= currentStep ? '#fff' : token.colorTextTertiary,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 11,
                  fontWeight: 600,
                  transition: 'background 0.3s, color 0.3s',
                  flexShrink: 0,
                }}
              >
                {submitted && i + 1 <= currentStep ? (
                  <CheckOutlined style={{ fontSize: 11 }} />
                ) : (
                  i + 1
                )}
              </div>
              {i < totalSteps - 1 && (
                <div
                  style={{
                    width: 24,
                    height: 2,
                    background: i + 1 < currentStep ? token.colorPrimary : token.colorFillSecondary,
                    transition: 'background 0.3s',
                  }}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {/* Page title */}
      <div style={{ padding: '12px 20px 0' }}>
        <Typography.Text strong style={{ fontSize: 15, color: token.colorText, display: 'block' }}>
          {totalSteps > 1
            ? `Step ${currentStep}/${totalSteps}: ${currentPage.title}`
            : currentPage.title}
        </Typography.Text>
        {currentPage.description && (
          <Typography.Text
            type="secondary"
            style={{ fontSize: 12, display: 'block', marginTop: 2 }}
          >
            {currentPage.description}
          </Typography.Text>
        )}
      </div>

      {/* Fields */}
      <div style={{ padding: '16px 20px' }}>{currentPage.fields.map(renderField)}</div>

      {/* Navigation buttons */}
      {!submitted && (
        <div
          style={{
            padding: '0 20px 16px',
            display: 'flex',
            justifyContent: isFirstPage ? 'flex-end' : 'space-between',
          }}
        >
          {!isFirstPage && (
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={handlePrev}
              style={{
                borderRadius: 8,
                height: 38,
                fontSize: 13,
                fontWeight: 500,
              }}
            >
              Back
            </Button>
          )}
          {isLastPage ? (
            <Button
              type="primary"
              icon={submitting ? <LoadingOutlined /> : <ArrowRightOutlined />}
              onClick={handleSubmit}
              loading={submitting}
              style={{
                borderRadius: 8,
                height: 38,
                fontSize: 13,
                fontWeight: 500,
                paddingLeft: 20,
                paddingRight: 20,
              }}
            >
              {formData.submit_label || 'Submit'}
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<ArrowRightOutlined />}
              onClick={handleNext}
              style={{
                borderRadius: 8,
                height: 38,
                fontSize: 13,
                fontWeight: 500,
                paddingLeft: 20,
                paddingRight: 20,
              }}
            >
              Continue
            </Button>
          )}
        </div>
      )}

      {/* Submitted state */}
      {submitted && (
        <div
          style={{
            padding: '10px 20px',
            background: token.colorSuccessBg,
            borderTop: `1px solid ${token.colorSuccessBorder}`,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 12,
            color: token.colorSuccess,
          }}
        >
          <CheckOutlined />
          Submitted
        </div>
      )}
    </div>
  );
}

function FieldLabel({
  label,
  required,
  token,
}: {
  label: string;
  required?: boolean;
  token: ReturnType<typeof theme.useToken>['token'];
}) {
  return (
    <div
      style={{
        fontSize: 13,
        fontWeight: 500,
        color: token.colorText,
        marginBottom: 4,
      }}
    >
      {label}
      {required && <span style={{ color: token.colorError, marginLeft: 2 }}>*</span>}
    </div>
  );
}

function FieldError({ token }: { token: ReturnType<typeof theme.useToken>['token'] }) {
  return (
    <div style={{ fontSize: 11, color: token.colorError, marginTop: 3 }}>
      This field is required
    </div>
  );
}

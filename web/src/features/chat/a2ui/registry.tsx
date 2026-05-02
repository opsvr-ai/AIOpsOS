import type { ComponentType } from 'react';
import type { ResolvedComponent } from './types';
import { A2UIText } from './components/Text';
import { A2UIImage } from './components/Image';
import { A2UIRow } from './components/Row';
import { A2UIColumn } from './components/Column';
import { A2UICard } from './components/Card';
import { A2UIList } from './components/List';
import { A2UIDivider } from './components/Divider';
import { A2UITabs } from './components/Tabs';
import { A2UIModal } from './components/Modal';
import { A2UITextField } from './components/TextField';
import { A2UICheckBox } from './components/CheckBox';
import { A2UIButton } from './components/Button';
import { A2UISlider } from './components/Slider';
import { A2UIMultipleChoice } from './components/MultipleChoice';
import { A2UIDateTimeInput } from './components/DateTimeInput';
import { A2UITable } from './components/Table';
import { A2UIChart } from './components/Chart';
import { A2UIStatCard } from './components/StatCard';
import { A2UIProgressBar } from './components/ProgressBar';
import { A2UITag } from './components/Tag';
import { A2UIConfirmDialog } from './components/ConfirmDialog';
import { A2UICodeEditor } from './components/CodeEditor';

export type A2UIComponentProps = {
  node: ResolvedComponent;
  surfaceId: string;
};

const registry = new Map<string, ComponentType<A2UIComponentProps>>();

// Content
registry.set('Text', A2UIText);
registry.set('Image', A2UIImage);
// Layout
registry.set('Row', A2UIRow);
registry.set('Column', A2UIColumn);
registry.set('Card', A2UICard);
registry.set('List', A2UIList);
registry.set('Divider', A2UIDivider);
registry.set('Tabs', A2UITabs);
registry.set('Modal', A2UIModal);
// Interactive
registry.set('TextField', A2UITextField);
registry.set('CheckBox', A2UICheckBox);
registry.set('Button', A2UIButton);
registry.set('Slider', A2UISlider);
registry.set('MultipleChoice', A2UIMultipleChoice);
registry.set('DateTimeInput', A2UIDateTimeInput);
// Data Display & Ops
registry.set('Table', A2UITable);
registry.set('Chart', A2UIChart);
registry.set('StatCard', A2UIStatCard);
registry.set('ProgressBar', A2UIProgressBar);
registry.set('Tag', A2UITag);
registry.set('ConfirmDialog', A2UIConfirmDialog);
registry.set('CodeEditor', A2UICodeEditor);

export function getA2UIComponent(type: string): ComponentType<A2UIComponentProps> | undefined {
  return registry.get(type);
}

export function registerA2UIComponent(type: string, comp: ComponentType<A2UIComponentProps>): void {
  registry.set(type, comp);
}

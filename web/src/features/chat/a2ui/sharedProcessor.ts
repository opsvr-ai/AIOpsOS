import { A2UIMessageProcessor } from './processor';

let instance: A2UIMessageProcessor | null = null;

export function getSharedProcessor(): A2UIMessageProcessor {
  if (!instance) {
    instance = new A2UIMessageProcessor();
  }
  return instance;
}

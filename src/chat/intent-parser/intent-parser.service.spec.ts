import { IntentParserService } from './intent-parser.service';
import { TaskType } from '../../common/interfaces';

describe('IntentParserService', () => {
  let parser: IntentParserService;

  beforeEach(() => {
    parser = new IntentParserService();
  });

  it('命中选品关键词', () => {
    const result = parser.parse('帮我分析市场趋势和竞品');
    expect(result.taskType).toBe(TaskType.PRODUCT_RESEARCH);
  });

  it('命中订单关键词', () => {
    const result = parser.parse('这个订单什么时候发货');
    expect(result.taskType).toBe(TaskType.ORDER_MANAGEMENT);
  });

  it('命中客服关键词', () => {
    const result = parser.parse('客户投诉了,帮我翻译一下回复');
    expect(result.taskType).toBe(TaskType.CUSTOMER_SERVICE);
  });

  it('回归:多类关键词命中时按命中数取分,而非首个顺序命中', () => {
    const result = parser.parse('客户抱怨物流太慢,帮我写个安抚回复');
    expect(result.taskType).toBe(TaskType.CUSTOMER_SERVICE);
  });

  it('无关键词时兜底客服', () => {
    const result = parser.parse('今天天气怎么样');
    expect(result.taskType).toBe(TaskType.CUSTOMER_SERVICE);
    expect(result.extractedInput).toEqual({
      action: 'handle_query',
      text: '今天天气怎么样',
    });
  });
});

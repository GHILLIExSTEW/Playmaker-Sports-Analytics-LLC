export const membershipPlans = [
  { name: 'Monthly', price: '$19.99', duration: '1 month', planId: 'plan_0MxgyyucPbdQZ' },
  { name: 'Three months', price: '$49.99', duration: '3 months', planId: 'plan_sN89nOD7airIA' },
  { name: 'Annual', price: '$199.99', duration: '12 months', planId: 'plan_FAZPnnd4WxDOU' },
] as const

export const membershipPrices = membershipPlans.map((plan) => `${plan.price} every ${plan.duration}`).join(', ')

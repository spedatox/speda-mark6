<script setup>
defineProps({ report: Object, t: Function, fmt: Function })
</script>
<template>
<section class="panel"><h2>{{ t('Source-supported account balances') }}</h2>
<p v-if="report?.provisional" class="notice">{{ t('History is incomplete. Unknown transfers and missing account history are not your assets or debt. A current net worth cannot be calculated yet.') }}</p>
<p>{{ t('Each balance below shows its source date. Later recorded movements do not establish a current balance when purchases or opening balances are missing.') }}</p>
<div v-for="a in report?.financial_accounts" :key="a.account_id" class="balance-row"><span>{{a.name}}<small>{{a.verified_through ? t('Reconciled through')+' '+a.verified_through : t('No reconciled source balance')}}</small></span><strong>{{a.source_balance===null ? t('Unknown balance') : fmt(a.source_balance)+' '+a.currency}}<small v-if="!a.coverage_complete">{{ t('Current balance unverified') }}</small></strong></div>
<details v-if="report?.review_accounts?.length"><summary>{{ t('Transfers and opening balances awaiting review') }} ({{report.review_accounts.length}})</summary><p>{{ t('These are incomplete bookkeeping offsets, not amounts you own or owe. They are excluded from the financial accounts above.') }}</p><div v-for="a in report.review_accounts" :key="a.account_id" class="balance-row"><span>{{a.name}}<small>{{a.report_note}}</small></span><strong>{{fmt(a.amount)}} {{a.currency}}<small>{{ t('Bookkeeping offset') }}</small></strong></div></details>
</section>
</template>

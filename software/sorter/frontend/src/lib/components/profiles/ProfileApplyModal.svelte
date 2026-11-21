<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import KeyValue from '$lib/components/ui/KeyValue.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import type { PendingProfileApply } from '$lib/sorting-profiles/types';

	type Props = {
		open: boolean;
		pending: PendingProfileApply | null;
		onConfirm: () => void;
		onCancel: () => void;
	};

	let { open = $bindable(), pending, onConfirm, onCancel }: Props = $props();
</script>

<Modal bind:open title={$_('components.profiles.profileApplyModal.modalTitle')}>
	{#if pending}
		<div class="flex flex-col gap-4">
			<Alert tone="warning" title={$_('components.profiles.profileApplyModal.warningTitle')}>
				{$_('components.profiles.profileApplyModal.warningBody')}
			</Alert>
			<KeyValue
				items={[
					{ label: $_('components.profiles.profileApplyModal.targetLabel'), value: pending.target_name },
					{ label: $_('components.profiles.profileApplyModal.profileLabel'), value: pending.profile_name },
					{
						label: $_('components.profiles.profileApplyModal.versionLabel'),
						value: `v${pending.version_number ?? '?'}${pending.version_label ? ` - ${pending.version_label}` : ''}`
					}
				]}
			/>
		</div>
	{/if}
	{#snippet footer()}
		<Button variant="ghost" onclick={onCancel}>{$_('components.profiles.profileApplyModal.cancelButton')}</Button>
		<Button variant="primary" onclick={onConfirm}
			>{$_('components.profiles.profileApplyModal.confirmButton')}</Button
		>
	{/snippet}
</Modal>

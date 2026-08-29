import ChatWindow from "@/components/chat/ChatWindow";

// Next 15 made `params` a Promise. A synchronous signature is a hard build
// error here ("is missing the following properties from type 'Promise<any>'"),
// not a silent break.
export default async function ConversationPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  return <ChatWindow initialConversationId={conversationId} />;
}

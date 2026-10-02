import DocumentsExplorer from "@/components/documents/DocumentsExplorer";

/** 문서 등록 - 내 개인 문서(0028). 올린 사람만 보고 검색한다. */
export default function MyDocumentsPage() {
  return <DocumentsExplorer scope="mine" />;
}
